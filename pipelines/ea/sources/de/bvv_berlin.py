"""Elections to the twelve Bezirksverordnetenversammlungen (borough assemblies) of Berlin, by Wahlbezirk.

The Amt für Statistik Berlin-Brandenburg publishes, for every election, one workbook with the result in each polling
district and each postal district (Wahlbezirk), and the Landeswahlleiter's results site publishes a page for each
Bezirk with its official totals and its seats. Each Bezirk is one contest, held at the same time as the election to
the Abgeordnetenhaus. The Bezirke are the de.bezirk areas the archive already holds.
"""

import html
import json
import re
from datetime import date
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package

SOURCE = "de.bvv_berlin"
BERLIN = "01a13bb5-c563-741b-83f7-4b80c9e09ccb"
SCHEME = "de.be_wahlbezirk"
BEZIRKE = {
    "01": ("11001", "01a13bb5-c57a-72d2-9042-3beb361267f3", "Mitte", "Mitte"),
    "02": ("11002", "01a13bb5-c57a-72d2-9042-3bec8786786f", "Friedrichshain-Kreuzberg", "Friedrichshain-Kreuzberg"),
    "03": ("11003", "01a13bb5-c57a-72d2-9042-3bed0f9dc951", "Pankow", "Pankow"),
    "04": ("11004", "01a13bb5-c57a-72d2-9042-3beec617b1e7", "Charlottenburg-Wilmersdorf", "Charlottenburg-Wilmersdorf"),
    "05": ("11005", "01a13bb5-c57a-72d2-9042-3bef71e3584e", "Spandau", "Spandau"),
    "06": ("11006", "01a13bb5-c57a-72d2-9042-3bf079d9e2f0", "Steglitz-Zehlendorf", "Steglitz-Zehlendorf"),
    "07": ("11007", "01a13bb5-c57a-72d2-9042-3bf1f68aeb10", "Tempelhof-Schöneberg", "Tempelhof-Schöneberg"),
    "08": ("11008", "01a13bb5-c57a-72d2-9042-3bf247220add", "Neukölln", "Neukölln"),
    "09": ("11009", "01a13bb5-c57a-72d2-9042-3bf3ee048d7c", "Treptow-Köpenick", "Treptow-Köpenick"),
    "10": ("11010", "01a13bb5-c57a-72d2-9042-3bf4dbb6c529", "Marzahn-Hellersdorf", "Marzahn-Hellersdorf"),
    "11": ("11011", "01a13bb5-c57a-72d2-9042-3bf53af5ba54", "Lichtenberg", "Lichtenberg"),
    "12": ("11012", "01a13bb5-c57a-72d2-9042-3bf6ce0efdcc", "Reinickendorf", "Reinickendorf"),
}
DOWNLOADS = "https://download.statistik-berlin-brandenburg.de"
ELECTIONS: dict[int, dict[str, str | date]] = {
    2023: {
        "day": date(2023, 2, 12),
        "xlsx": f"{DOWNLOADS}/c6fffa8361dd1404/a8cc1bc593d9/DL_BE_AGHBVV2023.xlsx",
        "pages": "https://www.wahlen-berlin.de/wahlen/BE2023/AFSPRAES/bvv/ergebnisse_bezirk_{nn}.html",
        "title": "Wiederholungswahl zu den Bezirksverordnetenversammlungen am 12. Februar 2023",
    },
    2026: {
        "day": date(2026, 9, 20),
        "xlsx": f"{DOWNLOADS}/933b7f91f10a6353/2fe6acfee545/DL_BE_AGHBVV2026.xlsx",
        "pages": "https://wahlen-berlin.de/wahlen/BE2026/Afspraes/BVV/ergebnisse_bezirk_{nn}.html",
        "title": "Wahlen zu den Bezirksverordnetenversammlungen am 20. September 2026",
    },
}
SERIES = f"{DOWNLOADS}/c8700cf5cd3c1777/4a7c5e7e7397/BVV-Wahlen_LangeReihe_2026_Berlin.xlsx"
SYSTEM = "sainte_lague_schepers"
RULES = {"formula": SYSTEM, "threshold": {"percent": 3, "level": "list", "scope": "contest"}}
SYSTEM_NOTE = (
    "Each voter has one vote, for a list of a party or a voters' association (Wählergemeinschaft) in the Bezirk. The "
    "55 seats of the Bezirksverordnetenversammlung are divided among the lists that won at least 3 percent of the valid "
    "votes by the Sainte-Laguë/Schepers method."
)
METHOD = (
    "Counted by the Bezirkswahlamt of each Bezirk and published by the Amt für Statistik Berlin-Brandenburg for each "
    "polling district (Urnenwahlbezirk) and postal district (Briefwahlbezirk). Registered voters are the electoral "
    "roll of a polling district; postal districts have no roll of their own. Voters are those who took part, and "
    "valid votes are the valid votes for lists. The source also splits out the voters with a voting certificate; they "
    "are included in the voters. Seats and the official totals of the Bezirk come from the Landeswahlleiter's page "
    "for the Bezirk."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source")


def norm(text: object) -> str:
    return re.sub(r"\s+", " ", str(text).replace("\xa0", " ")).strip()


def page_url(year: int, nn: str) -> str:
    return str(ELECTIONS[year]["pages"]).format(nn=nn)


def fetch() -> None:
    raw.fetch(SOURCE, SERIES, suffix=".xlsx")
    for year, spec in ELECTIONS.items():
        raw.fetch(SOURCE, str(spec["xlsx"]), suffix=".xlsx")
        for nn in BEZIRKE:
            raw.fetch(SOURCE, page_url(year, nn), suffix=".html")


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/bvv-{year}" for year in ELECTIONS)]


def parse() -> None:
    registry().write()
    for year in ELECTIONS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


def _sheet(year: int, name: str) -> list[list[str | None]]:
    path = raw.cached(SOURCE, str(ELECTIONS[year]["xlsx"])).path
    frame = pl.read_excel(path, sheet_name=name, has_header=False, infer_schema_length=0)
    return [list(row) for row in frame.iter_rows()]


def _int(value: object) -> int:
    return int(float(str(value))) if value not in (None, "") else 0


class Lists:
    """The lists of one election: their numbers, names, and the Bezirke they ran in, in the order of the workbook."""

    def __init__(self, year: int) -> None:
        rows = _sheet(year, "BVV_Kandidaturen")
        self.items: list[tuple[str, str, list[str]]] = []
        for row in rows:
            if row[0] is not None and re.fullmatch(r"\d\d", norm(row[0])):
                where = [f"{n + 1:02d}" for n, cell in enumerate(row[2:14]) if cell and norm(cell) == "X"]
                self.items.append((norm(row[0]), norm(row[1]), where))
        head = _sheet(year, "BVV")[0]
        columns = [norm(cell) for cell in head[18:] if cell is not None]
        if len(columns) != len(self.items):
            raise ValueError(f"{year}: {len(columns)} columns, {len(self.items)} lists")
        for column, (number, name, _) in zip(columns, self.items, strict=True):
            if column != name:
                raise ValueError(f"{year}: list {number} is {name!r} in the lists and {column!r} in the results")
        self.names = {number: name for number, name, _ in self.items}

    def party(self, number: str) -> str:
        """The key of the party behind a list: the label alone for a party, the label and Bezirk for a voters' list."""
        _, name, where = next(item for item in self.items if item[0] == number)
        duplicated = sum(1 for _, other, _ in self.items if other == name) > 1
        if name.startswith("WB ") or duplicated:
            return f"{name}:{where[0]}"
        return name


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Berlin borough assemblies (BVV), their electoral system, and the parties and voters' associations that ran",
    )
    p.external.add(BERLIN)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Amt für Statistik Berlin-Brandenburg (Landeswahlleiter für Berlin)",
        title="Wahlen zu den Bezirksverordnetenversammlungen in Berlin",
        url="https://www.statistik-berlin-brandenburg.de/abgeordnetenhauswahlen-bvv-berlin",
        license="Creative Commons Namensnennung 3.0 Deutschland",
        license_url="http://creativecommons.org/licenses/by/3.0/de/",
        attribution="Amt für Statistik Berlin-Brandenburg",
    )
    p.add("scheme", code=SCHEME, polity_id=BERLIN, kind="precinct", rank=5, noun="Wahlbezirk", code_system=None)
    for nn, (_, area, name, _) in BEZIRKE.items():
        p.external.add(area)
        body = key("body", nn)
        p.add("body", id=body, polity_id=BERLIN, kind="council")
        p.name(body, f"Borough Assembly of {name}", "en", "official")
        p.name(body, f"Bezirksverordnetenversammlung {name}", "de", "native", False)
        p.add("body_size", body_id=body, seats=55, valid_from=None, valid_to=None)
    system = key("electoral_system", SYSTEM, 3)
    p.add(
        "electoral_system",
        id=system,
        polity_id=BERLIN,
        valid_from=None,
        valid_to=None,
        rules=RULES,
        rules_version=1,
    )
    p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_NOTE, lang="en")
    labels: dict[str, tuple[str, str]] = {}
    for year in ELECTIONS:
        lists = Lists(year)
        for number, name, _ in lists.items:
            labels[lists.party(number)] = (name, "independent_group" if name.startswith("WB ") else "party")
        p.uses(raw.cached(SOURCE, str(ELECTIONS[year]["xlsx"])))
    for tag, (name, kind) in sorted(labels.items()):
        id = party(tag)
        p.add("party", id=id, kind=kind, polity_id=BERLIN)
        p.add("external_id", entity_id=id, scheme="de.bvv_berlin.list", value=tag)
        p.name(id, name, "de", "official")
    return p


def party(tag: str) -> str:
    return key("party", tag)


def _chart(text: str) -> list[dict[str, Any]]:
    """The seat chart of a Bezirk page: each list's label, full name, and seats."""
    for match in re.finditer(r'data-chartdata="([^"]*)"', text):
        data = json.loads(html.unescape(match.group(1)))
        sets = data.get("dataSets")
        if sets and all("Sitz" in str(s.get("tooltipData", {}).get("text")) for s in sets):
            return sets
    raise LookupError("no seat chart")


def _page(year: int, nn: str) -> tuple[dict[str, int], dict[str, int]]:
    """Seats by list label and the declared figures of one Bezirk page."""
    text = raw.cached(SOURCE, page_url(year, nn)).path.read_text(encoding="utf-8", errors="replace")
    seats = {norm(s["label"]): int(s["value"]) for s in _chart(text)}
    plain = re.sub(r"<script.*?</script>|<style.*?</style>", " ", text, flags=re.DOTALL)
    plain = norm(html.unescape(re.sub(r"<[^>]+>", " ", plain)))
    start = plain.index("Wahlberechtigte")
    body = plain[start:]
    declared: dict[str, int] = {}
    for label, key_ in (
        ("Wahlberechtigte", "registered"),
        ("Wählende", "voters"),
        ("Ungültige Stimmen", "invalid"),
        ("Gültige Stimmen", "valid"),
    ):
        found = re.search(rf"{re.escape(label)} ([\d.]+)", body)
        if found is None:
            raise LookupError(f"{year} {nn}: no {label}")
        declared[key_] = int(found.group(1).replace(".", ""))
    after = body[body.index("Gültige Stimmen") :]
    for label in seats:
        found = re.search(rf"(?:^| ){re.escape(label)} ([\d.]+) [\d,]+ %", after)
        if found is not None:
            declared[label] = int(found.group(1).replace(".", ""))
    return seats, declared


def election(year: int) -> Package:
    spec = ELECTIONS[year]
    day = spec["day"]
    assert isinstance(day, date)
    p = Package(
        f"{SOURCE}/bvv-{year}",
        SOURCE,
        f"The {day} elections to the twelve Berlin borough assemblies, by polling and postal district",
        depends=[f"{SOURCE}/registry"],
    )
    p.external.add(BERLIN)
    p.artifact(raw.cached(SOURCE, str(spec["xlsx"])), SOURCE_ID)
    election_id = key("election", day)
    p.add("election", id=election_id, polity_id=BERLIN, kind="local", date_from=day, date_to=day, status="held")
    p.name(election_id, f"{day:%B} {day.day}, {day.year} Berlin borough assembly elections", "en", "official")
    p.name(election_id, str(spec["title"]), "de", "native", False)
    path = f"de/{day}-be-bvv"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    lists = Lists(year)
    system = key("electoral_system", SYSTEM, 3)
    rows = _sheet(year, "BVV")
    by_bezirk: dict[str, list[list[str | None]]] = {}
    for row in rows[1:]:
        if row[1] is None:
            continue
        by_bezirk.setdefault(str(row[2]).zfill(2), []).append(row)
    for nn, (_, area, name, _) in BEZIRKE.items():
        contest = key("contest", day, nn)
        p.external.add(area)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation",
            body_id=key("body", nn),
            district_area_id=area,
            magnitude=55,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, f"Bezirksverordnetenversammlung {name}", "de", "official")
        p.add("slug", path=f"{path}/{slug(name)}", entity_id=contest, is_current=True)
        result_set = key("result_set", day, nn)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        page = raw.cached(SOURCE, page_url(year, nn))
        p.artifact(page, SOURCE_ID)
        seats, declared = _page(year, nn)
        ran = [(n, lists.names[n]) for n, _, where in lists.items if nn in where]
        options: dict[str, str] = {}
        for number, label in ran:
            option = options[number] = key("option", day, nn, number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(lists.party(number)),
                ballot_position=int(number),
                ballot_label=label,
            )
        if len(seats) != len(ran):
            raise ValueError(f"{year} {nn}: {len(seats)} lists in the page, {len(ran)} in the workbook")
        labels: dict[str, str] = {}
        for label, (number, name) in zip(seats, ran, strict=True):
            if not name.startswith("WB ") and label != name:
                raise ValueError(f"{year} {nn}: {label!r} on the page, {name!r} in the workbook")
            labels[label] = number
        for label, count in seats.items():
            if count:
                p.add("outcome", result_set_id=result_set, option_id=options[labels[label]], kind="won", seats=count)
        for measure, figure in ((2, "registered"), (3, "voters"), (4, "valid"), (5, "invalid")):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=declared[figure])
        for label, votes in declared.items():
            if label in labels:
                p.add("declared", result_set_id=result_set, option_id=options[labels[label]], measure=1, value=votes)
        columns = {
            number: 18 + position for position, (number, _, _) in enumerate(lists.items)
        }  # the workbook holds the lists in order
        for row in by_bezirk[nn]:
            code = f"{day}:{row[1]}"
            unit = key("unit", code)
            postal = row[5] == "B"
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=BERLIN,
                scheme_code=SCHEME,
                code=code,
                area_relation="within",
                area_id=area,
                kind="postal_district" if postal else "standard",
            )
            figures = [(2, 10), (3, 14), (4, 16), (5, 17)]
            for measure, column in figures:
                if measure == 2 and postal:
                    continue
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=_int(row[column]),
                )
            for number, option in options.items():
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=_int(row[columns[number]]),
                )
    return p
