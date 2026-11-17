"""Finland's popular votes for presidential electors, 1925 to 1988, from Statistics Finland's printed volumes.

From 1919 to 1987 the President of Finland was chosen by electors (valitsijamiehet) whom the voters elected by
constituency from party lists, 300 of them and 301 in 1982 and 1988. Statistics Finland printed each poll in the
Official Statistics of Finland (series XXIX A), and its library serves the scanned volumes. Each figure here was read
from the page images of the constituency tables, checked against the rows and the national totals the volume prints,
and keeps the page and box it came from. The 1988 volume also prints the direct popular vote held at the same poll.
"""

import functools
import json
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.fi import vaalit

SOURCE = "fi.electors"
FINLAND = str(uuid5(package.NAMESPACE, "polity\x1fFI"))
CONTENT = "https://otos.stat.fi/server/api/core/bitstreams/{}/content"
ITEM = "https://otos.stat.fi/items/{}"
TABLES = Path(__file__).with_name("electors_tables")
SCHEME = "fi.vaalipiiri"
VOTES, REGISTERED, CAST, VALID, INVALID = 1, 2, 3, 4, 5
DIGITIZED = 8
TOTALS = (
    ("valid", VALID, "votes"),
    ("invalid", INVALID, "votes"),
    ("cast", CAST, "votes"),
    ("registered", REGISTERED, "turnout"),
)
PUBLISHER = "Tilastokeskus (Statistics Finland)"
LICENSE = "Public official statistics; facts reused with attribution"


@dataclass(frozen=True)
class Volume:
    year: int
    item: str
    bitstream: str
    title: str
    series: str


VOLUMES = (
    Volume(
        1925,
        "bb8245a0-1086-49e1-b6c9-c23b8c9be612",
        "ff72551c-d60f-4a4c-8d34-a4c1e24ff128",
        "Tasavallan presidentin valitsijamiesten vaalit vuonna 1925",
        "Suomen virallinen tilasto XXIX A",
    ),
    Volume(
        1931,
        "21d0afc1-b4e6-485a-8f6d-b5edd6dfd084",
        "c57fc9ed-347b-4fb3-ada4-de735c38d922",
        "Tasavallan presidentin valitsijamiesten vaalit vuonna 1931",
        "Suomen virallinen tilasto XXIX A",
    ),
    Volume(
        1937,
        "1ff19f58-d963-46af-a6e4-999021ccc19f",
        "8429d666-de77-422c-a41d-5b6db45d8428",
        "Tasavallan presidentin valitsijamiesten vaalit vuonna 1937",
        "Suomen virallinen tilasto XXIX A",
    ),
    Volume(
        1950,
        "2ee543ac-961f-41d8-a702-f8ce17bd1740",
        "83fcdf14-06f0-4609-b822-07953bf8e60d",
        "Tasavallan presidentin valitsijamiesten vaalit vuonna 1950",
        "Suomen virallinen tilasto XXIX A",
    ),
    Volume(
        1956,
        "6a8b5cb7-112a-43be-92ca-a8b23da8406b",
        "95afd004-ee23-4d7a-87aa-501cf7a20523",
        "Tasavallan presidentin valitsijamiesten vaalit vuonna 1956",
        "Suomen virallinen tilasto XXIX A",
    ),
    Volume(
        1962,
        "609e4523-195b-4dd8-a877-6354340334e1",
        "54d68f55-9481-41d6-acbe-a5afe23ad427",
        "Tasavallan presidentin valitsijamiesten vaalit 1962",
        "Suomen virallinen tilasto XXIX A",
    ),
    Volume(
        1968,
        "7f19468f-ea67-4e41-b8f6-197142317129",
        "8c534210-34ff-4d23-9451-83962bdf291a",
        "Tasavallan presidentin valitsijamiesten vaalit 1968",
        "Suomen virallinen tilasto XXIX A",
    ),
    Volume(
        1978,
        "ee2689f1-dc2d-486d-81da-d3b206d15d31",
        "03b472f4-696e-4469-9296-01bd54a14a26",
        "Valtiolliset vaalit 1978: Tasavallan presidentin valitsijamiesten vaalit",
        "Suomen virallinen tilasto XXIX A",
    ),
    Volume(
        1982,
        "55f876f7-7f92-4394-aeb9-a8427860b0da",
        "10a6a66a-b1f9-49d3-ace0-6bc92445926b",
        "Valtiolliset vaalit 1982: Tasavallan presidentin valitsijamiesten vaalit",
        "Suomen virallinen tilasto XXIX A",
    ),
    Volume(
        1988,
        "c8c2cc72-f8a1-4cd8-8752-aef493cb84b7",
        "9f792029-7ac5-49cb-82ae-74851ae938e8",
        "Presidentin vaalit 1988",
        "Suomen virallinen tilasto, Vaalit",
    ),
)
YEARS = {volume.year: volume for volume in VOLUMES}
BODY_METHOD = (
    "The electors (valitsijamiehet) who chose the President of the Republic under the Constitution Act of 1919: "
    "300 electors, 301 in 1982 and 1988, elected by the voters in each constituency for one presidential "
    "election. The 1937 electors also chose the President in 1940 and 1943."
)
SYSTEM_METHOD = (
    "Each constituency elected its share of the electors, set by its population, from candidate lists that parties "
    "or groups of voters put forward. Lists could form electoral alliances (vaaliliitot), and the seats went to the "
    "lists and alliances by D'Hondt, as in parliamentary elections. The archive stores each party's votes and "
    "electors as the volume prints them, by party rather than by alliance, so the engine's allocation by party can "
    "differ from the declared one where alliances joined."
)
DIRECT_METHOD = (
    "The direct vote of 31 January and 1 February 1988: a candidate with more than half of the valid votes would "
    "have been elected; otherwise the 301 electors elected at the same poll chose the President. No candidate won a "
    "majority, and the electors elected Mauno Koivisto on 15 February 1988. The engine's rules name the legislature "
    "as the chooser because they have no value for an electoral college."
)


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in YEARS)]


def url(volume: Volume) -> str:
    return CONTENT.format(volume.bitstream)


def fetch() -> None:
    for volume in VOLUMES:
        raw.fetch(SOURCE, url(volume), suffix=".pdf")


@functools.cache
def table(year: int) -> dict[str, Any]:
    return json.loads((TABLES / f"{year}.json").read_text())


def slug(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", plain.casefold()).strip("-")


def district_code(name: str) -> str:
    """The unit code of a historical constituency: its Finnish name without the word for constituency."""
    return "e-" + slug(re.sub(r"\s*vaalipiiri\s*$", "", name))


def check(year: int) -> None:
    """Fail when a volume's rows disagree with its own totals, unless the misprint is listed in its table file."""
    data = table(year)
    allowed = set(data.get("allowed", []))
    problems: list[str] = []

    def row_check(name: str, row: dict[str, Any]) -> None:
        if sum(row["votes"].values()) != row["valid"]:
            problems.append(f"{name}: votes")
        if row["valid"] + row["invalid"] != row["cast"]:
            problems.append(f"{name}: ballots")

    for row in data["districts"]:
        row_check(row["fi"], row)
    row_check("national", data["national"])
    nat = data["national"]
    for field in ("registered", "cast", "valid", "invalid"):
        if sum(row[field] for row in data["districts"]) != nat[field]:
            problems.append(f"column {field}")
    for party in data["parties"]:
        k = party["key"]
        if sum(row["votes"].get(k, 0) for row in data["districts"]) != nat["votes"].get(k, 0):
            problems.append(f"party {k} votes")
        if sum(row.get("electors", {}).get(k, 0) for row in data["districts"]) != nat.get("electors", {}).get(k, 0):
            problems.append(f"party {k} electors")
    if sum(nat["electors"].values()) != data["electors"]:
        problems.append("electors")
    left = [problem for problem in problems if problem not in allowed]
    if left:
        raise ValueError(f"{year}: the transcription disagrees with the printed totals: {left}")


def parse() -> None:
    registry().write()
    for year in YEARS:
        check(year)
        build(year).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------------------------------------------------------------
# Registry


# Columns that are part of a party's lists rather than a party of their own, and columns of no party.
PARTY_OF: dict[str, str | None] = {
    "rkp_kekkonen": "rkp",
    "rkp_vennamo": "rkp",
    "rkp_virkkunen": "rkp",
    "kekkonen_union_rkp": "rkp",
    "kansanpuolue_kekkonen": "kansanpuolue",
    "aland": "al_samling",
    "alandsk_samling": "al_samling",
    "kok_ind": None,
    "kekkonen_union_ind": None,
    "kekkonen_other": None,
    "other": None,
    "written": None,
}
OTHERS = {"other", "written", "kekkonen_other"}


def party_of(key: str) -> str | None:
    return PARTY_OF.get(key, key)


def registry() -> Package:
    p = Package(f"{SOURCE}/registry", SOURCE, "The electors' college, its electoral system, and the parties that ran")
    p.external.add(FINLAND)
    body = p.id("body", "electors")
    p.add("body", id=body, polity_id=FINLAND, kind="electoral_college")
    p.name(body, "Presidential electors of Finland", "en", "official")
    p.name(body, "Tasavallan presidentin valitsijamiehet", "fi", "native", False)
    p.name(body, "Elektorerna för val av republikens president", "sv", "native", False)
    p.add("note", id=p.id("note", body), entity_id=body, kind="context", body_md=BODY_METHOD, lang="en")
    system = p.id("electoral_system", "electors")
    p.add(
        "electoral_system",
        id=system,
        polity_id=FINLAND,
        valid_from=date(1919, 7, 17),
        valid_to=date(1991, 1, 1),
        rules={"formula": "dhondt"},
        rules_version=1,
    )
    p.add("note", id=p.id("note", system), entity_id=system, kind="methodology", body_md=SYSTEM_METHOD, lang="en")
    direct = p.id("electoral_system", "direct-1988")
    p.add(
        "electoral_system",
        id=direct,
        polity_id=FINLAND,
        valid_from=date(1987, 1, 1),
        valid_to=date(1991, 1, 1),
        rules={"formula": "contingent", "majority": 0.5, "chooser": "legislature", "among": 1},
        rules_version=1,
    )
    p.add("note", id=p.id("note", direct), entity_id=direct, kind="methodology", body_md=DIRECT_METHOD, lang="en")
    seen: dict[str, list[tuple[int, dict[str, Any]]]] = {}
    for year in YEARS:
        for party in table(year)["parties"]:
            if party_of(party["key"]) == party["key"]:
                seen.setdefault(party["key"], []).append((year, party))
    for key, uses in sorted(seen.items()):
        id = p.id("party", key)
        p.add(
            "party", id=id, kind="list_vehicle" if key.endswith(("_group", "_general")) else "party", polity_id=FINLAND
        )
        p.add("external_id", entity_id=id, scheme="fi.electors.party", value=key)
        names: dict[tuple[str, str], int] = {}
        for year, party in uses:
            for lang in ("fi", "sv"):
                if party.get(lang):
                    names[(lang, party[lang])] = year
        newest = {
            lang: max((year, text) for (l, text), year in names.items() if l == lang)
            for lang in ("fi", "sv")
            if any(l == lang for l, _ in names)
        }
        for (lang, text), _ in sorted(names.items()):
            current = newest[lang][1] == text
            p.name(id, text, lang, "official" if current else "historical", current and lang == "fi")
    return p


# ---------------------------------------------------------------------------------------------------------------------
# Locating each figure on its page


class _Words(HTMLParser):
    """The words of one page of `pdftotext -bbox` output, with their boxes in points."""

    def __init__(self) -> None:
        super().__init__()
        self.words: list[tuple[str, float, float, float, float]] = []
        self.size = (1.0, 1.0)
        self._box: tuple[float, float, float, float] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {
            k: float(v) for k, v in attrs if v is not None and k in {"xmin", "ymin", "xmax", "ymax", "width", "height"}
        }
        if tag == "page":
            self.size = (values["width"], values["height"])
        elif tag == "word":
            self._box = (values["xmin"], values["ymin"], values["xmax"], values["ymax"])

    def handle_data(self, data: str) -> None:
        if self._box is not None:
            self.words.append((data.strip(), *self._box))
            self._box = None


@functools.cache
def words(path: Path, page: int) -> tuple[tuple[float, float], list[tuple[str, float, float, float, float]]]:
    out = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", str(path), "-"],
        capture_output=True,
        text=True,
        check=True,
    )
    parser = _Words()
    parser.feed(out.stdout)
    return parser.size, parser.words


Box = tuple[float, float, float, float]


@functools.cache
def candidates(path: Path, page: int) -> dict[str, list[Box]]:
    """Every run of up to three adjacent words on one line, by the digits it spells, with its box as page fractions."""
    (width, height), found = words(path, page)
    out: dict[str, list[Box]] = {}
    for i in range(len(found)):
        digits = ""
        x0, y0, x1, y1 = found[i][1:]
        for j in range(i, min(i + 3, len(found))):
            text, a0, b0, a1, b1 = found[j]
            if j > i and (
                abs((b0 + b1) / 2 - (y0 + y1) / 2) > (y1 - y0) * 0.6 or a0 - x1 > (y1 - y0) * 1.2 or a0 < x1 - 1
            ):
                break
            part = re.sub(r"[^0-9]", "", text)
            if not part or len(part) != len(re.sub(r"[.,\s]", "", text)):
                break
            digits += part
            x0, y0, x1, y1 = min(x0, a0), min(y0, b0), max(x1, a1), max(y1, b1)
            out.setdefault(digits, []).append((x0 / width, y0 / height, x1 / width, y1 / height))
    return out


def _middle(box: Box) -> float:
    return (box[1] + box[3]) / 2


def _median(values: list[float]) -> float:
    return sorted(values)[len(values) // 2]


def locate(path: Path, page: int, rows: list[list[int]]) -> list[list[Box]]:
    """The box of each figure of a table's rows on one page. A row's line is where most of its figures are found in
    the text layer; a figure the text layer misread gets the box where its row and its column cross, measured from
    the figures that were found."""
    found = candidates(path, page)
    picks: list[list[Box | None]] = []
    for row in rows:
        options = [found.get(str(value), []) for value in row]
        best: float | None = None
        score = 0
        for y in sorted(_middle(box) for boxes in options for box in boxes):
            n = sum(any(abs(_middle(box) - y) < 0.006 for box in boxes) for boxes in options)
            if n > score:
                best, score = y, n
        chosen: list[Box | None] = []
        for boxes in options:
            near = [box for box in boxes if best is not None and abs(_middle(box) - best) < 0.006]
            chosen.append(min(near, key=lambda box: abs(_middle(box) - (best or 0.0))) if near else None)
        picks.append(chosen)
    columns: list[tuple[float, float] | None] = []
    for c in range(max((len(row) for row in rows), default=0)):
        hits = [box for row in picks if c < len(row) for box in [row[c]] if box is not None]
        columns.append((_median([h[0] for h in hits]), _median([h[2] for h in hits])) if hits else None)
    out: list[list[Box]] = []
    for row in picks:
        hits = [box for box in row if box is not None]
        if hits:
            line = (_median([h[1] for h in hits]), _median([h[3] for h in hits]))
        else:
            above = next((done[0] for done in reversed(out) if done), None)
            line = (above[1], above[3]) if above else (0.0, 1.0)
        boxes: list[Box] = []
        for c, box in enumerate(row):
            span = columns[c]
            boxes.append(
                box
                if box is not None
                else (span[0], line[0], span[1], line[1])
                if span
                else (0.0, line[0], 1.0, line[1])
            )
        out.append(boxes)
    return out


# ---------------------------------------------------------------------------------------------------------------------
# Elections


def page_size(path: Path, page: int) -> tuple[float, float]:
    return words(path, page)[0]


def build(year: int) -> Package:
    data = table(year)
    volume = YEARS[year]
    day, last = date.fromisoformat(data["date_from"]), date.fromisoformat(data["date_to"])
    direct = data.get("direct")
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} popular vote for presidential electors by constituency, digitized from Statistics Finland's volume",
        depends=[f"{SOURCE}/registry", *(["fi.vaalit/registry"] if direct else [])],
    )
    p.external.update({FINLAND, SCHEME})
    source = p.id("source", year)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title=f"{volume.title} ({volume.series})",
        url=ITEM.format(volume.item),
        license=LICENSE,
        attribution=f"Tilastokeskus, {volume.series}: {volume.title}",
    )
    stored = raw.cached(SOURCE, url(volume))
    artifact = p.artifact(stored, source)
    pages: dict[int, str] = {}

    def page_id(number: int) -> str:
        if number not in pages:
            width, height = page_size(stored.path, number)
            pages[number] = p.id("page", year, number)
            p.add(
                "source_page",
                id=pages[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{stored.sha256}-{number}.png",
                width=round(width * 100 / 72),
                height=round(height * 100 / 72),
            )
        return pages[number]

    election = p.id("election", year)
    p.add(
        "election",
        id=election,
        polity_id=FINLAND,
        kind="general",
        date_from=day,
        date_to=last,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{year} Finnish presidential electors election", "en", "official")
    p.name(election, f"Tasavallan presidentin valitsijamiesten vaali {year}", "fi", "native", False)
    p.name(election, f"Val av elektorer för valet av republikens president {year}", "sv", "native", False)
    base = f"fi/{day}-presidential-electors"
    p.add("slug", path=base, entity_id=election, is_current=True)
    method = (
        f"Votes for the electors' lists by party and constituency, from the constituency tables of Statistics "
        f"Finland's volume {volume.title} ({volume.series}). Digitized from the scanned pages: every figure keeps its "
        f"page and box, and the constituency rows were checked against the national totals the volume prints. "
        f"Each constituency's figures include the ballots its voters cast in other constituencies. Electors are "
        f"stored as the seats each party won in the constituency, as the volume's table of electors elected prints "
        f"them. A figure's box comes from the scan's text layer where that layer reads it correctly; otherwise it is "
        f"where the figure's row and column cross. The volume also prints figures by municipality, which are not yet "
        f"stored."
    )
    if "written" in {party["key"] for party in data["parties"]}:
        method += (
            " Accepted handwritten candidate lists count only toward the valid ballots in the party table; their votes, "
            "which the volume prints in a separate table, are stored as one option of other lists."
        )
    if "valid" in data.get("unprinted", []):
        method += (
            " The volume prints no valid-ballot total by constituency, so none is stored; the party votes and the "
            "rejected ballots add up to the voters it prints."
        )
    body = p.id("body", "electors")
    system = p.id("electoral_system", "electors")
    parties = {party["key"]: party for party in data["parties"]}
    unprinted = set(data.get("unprinted", []))
    order = [party["key"] for party in data["parties"]]
    values: list[tuple[str, str, str | None, int, int]] = []
    locators: list[tuple[str, str, str | None, int, str, float, float, float, float]] = []
    tables: dict[tuple[str, int], list[tuple[str, str, list[tuple[str | None, int, int]]]]] = {}
    used: set[str] = set()
    for row in data["districts"]:
        code = district_code(row["fi"])
        unit = p.id("unit", code)
        if code not in used:
            used.add(code)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=FINLAND,
                scheme_code=SCHEME,
                code=code,
                area_relation="unlocated",
                area_id=None,
                kind="standard",
            )
            p.name(unit, row["fi"], "fi", "official")
            if row.get("sv"):
                p.name(unit, row["sv"], "sv", "official", False)
        seats = sum(row["electors"].values())
        contest = p.id("contest", year, code)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="elector_slate",
            body_id=body,
            magnitude=seats,
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, row["fi"], "fi", "official")
        if row.get("sv"):
            p.name(contest, row["sv"], "sv", "official", False)
        p.add(
            "slug",
            path=f"{base}/{slug(re.sub(r'\s*vaalipiiri\s*$', '', row['fi']))}",
            entity_id=contest,
            is_current=True,
        )
        result_set = p.id("result_set", year, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        options: dict[str, str] = {}
        for position, key in enumerate(k for k in order if k in row["votes"] or k in row["electors"]):
            option = options[key] = p.id("option", year, code, key)
            party = parties[key]
            owner = party_of(key)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="others" if key in OTHERS else "list",
                party_id=p.id("party", owner) if owner else None,
                ballot_position=position + 1,
                ballot_label=party["fi"],
            )
            if row["electors"].get(key):
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row["electors"][key])
        placed: dict[tuple[str, int], list[tuple[str | None, int, int]]] = {}
        for k in order:
            if k in row["votes"]:
                where = (k, row["pages"][k]) if k in row["pages"] else ("votes", row["pages"]["votes"])
                placed.setdefault(where, []).append((options[k], VOTES, row["votes"][k]))
        for field, measure, home in TOTALS:
            if field in unprinted:
                continue
            where = (field, row["pages"][field]) if field in row["pages"] else (home, row["pages"][home])
            placed.setdefault(where, []).append((None, measure, row[field]))
        for where, cells in placed.items():
            tables.setdefault(where, []).append((result_set, unit, cells))
    if direct:
        _direct(p, data, direct, election, base, source, tables)
    for (_, number), rows in sorted(tables.items()):
        boxes = locate(stored.path, number, [[value for _, _, value in cells] for _, _, cells in rows])
        for (result_set, unit, cells), row_boxes in zip(rows, boxes, strict=True):
            for (option, measure, value), box in zip(cells, row_boxes, strict=True):
                values.append((result_set, unit, option, measure, value))
                locators.append((result_set, unit, option, measure, page_id(number), *box))
    p.frame(
        "result_value",
        pl.DataFrame(
            values, schema=["result_set_id", "unit_id", "option_id", "measure", "value"], orient="row"
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    p.frame(
        "value_locator",
        pl.DataFrame(
            locators,
            schema=["result_set_id", "unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
            orient="row",
        ).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    for code, reason in data.get("accept", {}).items():
        p.accept(code, reason)
    return p


def _direct(
    p: Package,
    data: dict[str, Any],
    direct: dict[str, Any],
    election: str,
    base: str,
    source: str,
    tables: dict[tuple[str, int], list[tuple[str, str, list[tuple[str | None, int, int]]]]],
) -> None:
    """The direct popular vote of 1988, held with the electors' vote, as one national contest by constituency."""
    year = int(data["year"])
    contest = p.id("contest", year, "direct")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=vaalit.PRESIDENCY,
        magnitude=1,
        round_number=1,
        electoral_system_id=p.id("electoral_system", "direct-1988"),
        default_result_kind="counted",
    )
    p.name(contest, f"{year} Finnish presidential election, direct vote", "en", "official")
    p.name(contest, f"Presidentin vaali {year}, välitön kansanvaali", "fi", "native", False)
    p.add("slug", path=f"{base}/president", entity_id=contest, is_current=True)
    result_set = p.id("result_set", year, "direct")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=p.id("note", result_set),
        entity_id=result_set,
        kind="methodology",
        body_md=(
            f"Votes for each candidate by constituency in the direct vote, from Statistics Finland's volume "
            f"Presidentin vaalit 1988. Digitized from the scanned pages: every figure keeps its page and box. "
            f"{DIRECT_METHOD} No outcome is stored for the direct vote, since no candidate won it."
        ),
        lang="en",
    )
    options: dict[str, str] = {}
    for position, candidate in enumerate(direct["candidates"], 1):
        option = options[candidate["key"]] = p.id("option", year, "direct", candidate["key"])
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            party_id=p.id("party", candidate["party"]) if candidate.get("party") else None,
            ballot_position=position,
            ballot_label=candidate["name"],
        )
        person = p.id("person", candidate["key"])
        p.add("person", id=person)
        p.name(person, candidate["name"], "fi", "official")
        p.add(
            "candidacy",
            id=p.id("candidacy", year, candidate["key"]),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate["name"],
            member_of_party_id=p.id("party", candidate["party"]) if candidate.get("party") else None,
        )
    for row in direct["districts"]:
        unit = p.id("unit", district_code(row["fi"]))
        cells: list[tuple[str | None, int, int]] = [
            *(
                (options[c["key"]], VOTES, row["votes"][c["key"]])
                for c in direct["candidates"]
                if c["key"] in row["votes"]
            ),
            (None, VALID, row["valid"]),
            (None, INVALID, row["invalid"]),
            (None, CAST, row["cast"]),
        ]
        tables.setdefault(("direct", row["pages"]["votes"]), []).append((result_set, unit, cells))
    nat = direct["national"]
    for c in direct["candidates"]:
        p.add(
            "declared",
            result_set_id=result_set,
            option_id=options[c["key"]],
            measure=VOTES,
            value=nat["votes"][c["key"]],
        )
    for measure, field in ((VALID, "valid"), (INVALID, "invalid"), (CAST, "cast")):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=nat[field])
