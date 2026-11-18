"""New Zealand's general elections before 1999 from the official returns: the Chief Electoral Office's published
statistics of the 1996 election and, back to 1853, the returns printed in the Appendix to the Journals of the House
of Representatives (series H-33, later E-9).

The 1996 statistics are scanned pages, served as PDFs whose text layer is unreadable, so each figure was read from the
page image at 300 dpi with Tesseract, checked by eye against the page, and transcribed with its box in
`ajhr_1996.tsv` (in PDF points from the page's top left). Every row of party votes sums to its printed valid total,
every column sums to the printed totals of the general and Māori electorates and to the combined totals, and the
valid and informal votes of Part V agree with Part IX, so the transcription is checked on both axes before a package
is built.

The 1996 package holds the party vote by electorate, with each electorate's informal votes, total votes cast, and
electors on the master roll. The electorate candidate votes are printed only in the per-electorate polling-place
returns, which are not yet cached, and the Appendix's returns for 1853 to 1993 are not yet cached either.
"""

import csv
import functools
import re
import subprocess
from collections import defaultdict
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import uuid5

import httpx

from ea import package, raw
from ea.package import Package
from ea.sources.nz import ec

SOURCE = "nz.ajhr"
NEW_ZEALAND = str(uuid5(package.NAMESPACE, "polity\x1fNZ"))
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14
TRANSCRIPTION = Path(__file__).with_name("ajhr_1996.tsv")
SITE_1996 = "https://www.electionresults.govt.nz/electionresults_1996"
REGISTRY = "nz.ajhr/registry"
EC_REGISTRY = "nz.ec/registry"

DAY_1996 = date(1996, 10, 12)
PDF_1996 = {
    "summary": "1.1 Summary of overall results.pdf",
    "part9": "2.1 Party Votes and Turnout by Electorate.pdf",
    "part5": "4.1 Votes for Registered Parties by Electorate.pdf",
}
PAGES_1996 = (f"{SITE_1996}/index.html", f"{SITE_1996}/pollingplaces.html")

# The registered parties in the order Part V prints them, with the name each is known by. The printed names are in
# capitals; Republican Party stood electorate candidates only, so it has no party-vote column.
PARTIES = {
    "ACT NEW ZEALAND": "ACT New Zealand",
    "ADVANCE NZ": "Advance NZ",
    "ALLIANCE": "Alliance",
    "ANIMALS FIRST": "Animals First",
    "AOTEAROA LEGALISE CANNABIS PARTY": "Aotearoa Legalise Cannabis Party",
    "ASIA PACIFIC UNITED PARTY": "Asia Pacific United Party",
    "CHRISTIAN COALITION": "Christian Coalition",
    "ETHNIC MINORITY PARTY": "Ethnic Minority Party",
    "GREEN SOCIETY": "Green Society",
    "LABOUR PARTY": "Labour Party",
    "MANA MAORI MOVEMENT": "Mana Maori Movement",
    "McGILLICUDDY SERIOUS": "McGillicuddy Serious",
    "NATURAL LAW PARTY": "Natural Law Party",
    "NZ CONSERVATIVE PARTY": "NZ Conservative Party",
    "NEW ZEALAND FIRST PARTY": "New Zealand First Party",
    "NZ NATIONAL PARTY": "NZ National Party",
    "NZ SUPERANNUITANTS AND YOUTH ACTION": "NZ Superannuitants and Youth Action",
    "PROGRESSIVE GREENS": "Progressive Greens",
    "TE TAWHARAU": "Te Tawharau",
    "THE LIBERTARIANZ": "The Libertarianz",
    "UNITED NEW ZEALAND PARTY": "United New Zealand Party",
}
# Part I's state of the parties: the list seats allocated and the electorate seats won, 120 in all.
SEATS_1996 = {
    "ACT NEW ZEALAND": (7, 1),
    "ALLIANCE": (12, 1),
    "LABOUR PARTY": (11, 26),
    "NEW ZEALAND FIRST PARTY": (11, 6),
    "NZ NATIONAL PARTY": (14, 30),
    "UNITED NEW ZEALAND PARTY": (0, 1),
}
TOTALS = ("General Electorate Totals", "Maori Electorate Totals", "Combined Totals")

SOURCE_1996 = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fceo-1996"))


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def pdf_url(name: str) -> str:
    return f"{SITE_1996}/pdf/{quote(name)}"


def polling_place_urls() -> list[str]:
    """The per-electorate polling-place returns of 1996, as the polling-places page links them."""
    page = art(PAGES_1996[1]).path.read_text(errors="replace")
    return [pdf_url(name) for name in re.findall(r'href="pdf/(6\.1 [^"]+\.pdf)"', page)]


@functools.cache
def _held() -> dict[str, str]:
    """The cache's entries by the address the authority serves them from, whether fetched directly or by snapshot."""
    found: dict[str, str] = {}
    for stored in raw.keys(SOURCE):
        original = stored.split("id_/", 1)[1] if "web.archive.org/web/" in stored and "id_/" in stored else stored
        found[_plain(original)] = stored
    return found


def _plain(url: str) -> str:
    return url.replace("://www.", "://").replace("%20", " ")


def art(url: str) -> raw.Artifact:
    stored = _held().get(_plain(url))
    if stored is None:
        raise LookupError(f"{url} is not in the {SOURCE} raw cache; run `ea fetch {SOURCE}` first")
    return raw.cached(SOURCE, stored)


def has(url: str) -> bool:
    return _plain(url) in _held()


def fetch() -> None:
    wanted = [(url, ".html") for url in PAGES_1996] + [(pdf_url(name), ".pdf") for name in PDF_1996.values()]
    for url, suffix in wanted:
        _try(url, suffix)
    _held.cache_clear()
    if has(PAGES_1996[1]):
        for url in polling_place_urls():
            _try(url, ".pdf")


def _try(url: str, suffix: str) -> None:
    if has(url):
        return
    try:
        raw.fetch(SOURCE, url, suffix=suffix)
    except httpx.HTTPError as failure:
        print(f"{url}: {failure}", flush=True)


def packages() -> list[str]:
    return [REGISTRY, f"{SOURCE}/{DAY_1996.isoformat()}"]


def parse() -> None:
    registry().write()
    general_1996().write()


def emit() -> None:
    package.run("load", packages())


def party(label: str) -> str:
    return key("party", label)


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The sources and parties of New Zealand's general elections before 1999, from the official returns",
    )
    p.external.add(NEW_ZEALAND)
    p.add(
        "source",
        id=SOURCE_1996,
        kind="official_body",
        publisher="Chief Electoral Office",
        title="1996 General Election: official results and statistics",
        url=f"{SITE_1996}/index.html",
        license="Crown copyright; reproduction for private study, research, criticism, or review is permitted",
        attribution="Chief Electoral Office 1997",
    )
    for label, name in PARTIES.items():
        id = party(label)
        p.add("party", id=id, kind="party", polity_id=NEW_ZEALAND)
        p.name(id, name, "en", "official")
    return p


Table = dict[tuple[str, str], dict[str, tuple[int, int, tuple[float, float, float, float]]]]


@functools.cache
def transcription() -> Table:
    """Each printed figure of the 1996 tables, by table and row, then column: its value, page, and box."""
    found: Table = defaultdict(dict)
    with TRANSCRIPTION.open() as file:
        for row in csv.DictReader(file, delimiter="\t"):
            box = (float(row["x0"]), float(row["y0"]), float(row["x1"]), float(row["y1"]))
            cell = found[(row["table"], row["row"])]
            if row["column"] in cell:
                raise ValueError(f"{TRANSCRIPTION.name}: {row['table']} {row['row']} {row['column']} is twice")
            cell[row["column"]] = (int(row["value"]), int(row["page"]), box)
    return dict(found)


def electorates() -> list[str]:
    return [row for table, row in transcription() if table == "part5" and row not in TOTALS]


def check_1996() -> dict[str, int]:
    """Check the transcription against every sum the pages print, and return the combined totals of Part V."""
    t = transcription()
    names = electorates()
    if len(names) != 65 or {row for table, row in t if table == "part9"} != {*names, *TOTALS}:
        raise ValueError(f"1996: Part V and Part IX list different electorates ({len(names)} in Part V)")
    for name in names:
        five = {column: cell[0] for column, cell in t[("part5", name)].items()}
        nine = {column: cell[0] for column, cell in t[("part9", name)].items()}
        if sum(five[label] for label in PARTIES) != five["valid"]:
            raise ValueError(f"1996 {name}: the party votes do not sum to the printed valid votes")
        if (five["valid"], five["informal"]) != (nine["valid"], nine["informal"]):
            raise ValueError(f"1996 {name}: Part V and Part IX give different valid or informal votes")
        if (
            nine["ordinary_valid"] + nine["special_valid"] != nine["valid"]
            or nine["ordinary_informal"] + nine["special_informal"] != nine["informal"]
            or nine["valid"] + nine["informal"] + nine["disallowed"] != nine["cast"]
        ):
            raise ValueError(f"1996 {name}: Part IX's columns do not add up")
    maori = [name for name in names if name.startswith("Te ")]
    groups = {
        "General Electorate Totals": [name for name in names if name not in maori],
        "Maori Electorate Totals": maori,
        "Combined Totals": names,
    }
    for table in ("part5", "part9"):
        for total, members in groups.items():
            for column, (printed, _, _) in t[(table, total)].items():
                summed = sum(t[(table, name)][column][0] for name in members)
                if summed != printed:
                    raise ValueError(f"1996 {table} {total} {column}: the electorates sum to {summed}, not {printed}")
    return {column: cell[0] for column, cell in t[("part5", "Combined Totals")].items()}


@functools.cache
def page_size(path: Path, page: int) -> tuple[int, int]:
    out = subprocess.run(
        ["pdfinfo", "-f", str(page), "-l", str(page), str(path)], capture_output=True, text=True, check=True
    ).stdout
    found = re.search(rf"Page\s+{page} size:\s+([\d.]+) x ([\d.]+)", out)
    if found is None:
        raise ValueError(f"{path}: page {page} has no size")
    return round(float(found.group(1))), round(float(found.group(2)))


def general_1996() -> Package:
    day = DAY_1996.isoformat()
    combined = check_1996()
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        "The 1996 New Zealand general election: the party vote by electorate, from the Chief Electoral Office's "
        "official statistics",
        depends=[REGISTRY, EC_REGISTRY],
    )
    p.external.add(NEW_ZEALAND)
    artifacts = {table: art(pdf_url(name)) for table, name in PDF_1996.items()}
    artifact_ids = {table: p.artifact(found, SOURCE_1996) for table, found in artifacts.items()}
    for url in PAGES_1996:
        p.artifact(art(url), SOURCE_1996)

    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=NEW_ZEALAND,
        kind="general",
        date_from=DAY_1996,
        date_to=DAY_1996,
        status="held",
    )
    p.add("slug", path=f"nz/{day}-general", entity_id=election, is_current=True)
    p.name(election, "1996 New Zealand general election", "en", "official")
    p.add("citation", id=key("citation", election), entity_id=election, source_id=SOURCE_1996, locator=None)

    contest = key("contest", day, "party")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=ec.PARLIAMENT,
        magnitude=sum(sum(seats) for seats in SEATS_1996.values()),
        round_number=1,
        electoral_system_id=ec.SYSTEM_PARTY,
        default_result_kind="counted",
    )
    p.name(contest, "1996 New Zealand general election, party vote", "en", "official")
    p.add("slug", path=f"nz/{day}-general/party-vote", entity_id=contest, is_current=True)
    result_set = key("result_set", day, "party")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=SOURCE_1996,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add(
        "note",
        id=key("note", result_set, "method"),
        entity_id=result_set,
        kind="methodology",
        body_md=(
            "Read from the scanned pages of the Chief Electoral Office's 1996 statistics: the party votes, valid "
            "votes, and informal votes from Part V, and the total votes cast and electors on the master roll from "
            "Part IX. Each figure keeps its page and box. The party votes of every electorate sum to its printed "
            "valid votes, and every column sums to the printed totals of the general and Māori electorates. The "
            "total votes cast include the special votes disallowed. The Māori electorates include their share of "
            "the Tangata Whenua votes."
        ),
        lang="en",
    )
    p.add(
        "note",
        id=key("note", result_set, "scope"),
        entity_id=result_set,
        kind="caveat",
        body_md=(
            "Only the party vote by electorate is held. The electorate candidate votes, and the counts at each "
            "polling place, are printed in the polling-place returns, which are not yet held."
        ),
        lang="en",
    )

    options: dict[str, str] = {}
    for position, label in enumerate(PARTIES, 1):
        option = options[label] = key("option", day, "party", label)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="list",
            party_id=party(label),
            ballot_position=position,
            ballot_label=label,
        )
    for label, (listed, won) in SEATS_1996.items():
        p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=listed + won)
    p.add(
        "note",
        id=key("note", contest, "seats"),
        entity_id=contest,
        kind="context",
        body_md=(
            "Seats are each party's total in the House, as Part I's state of the parties prints them: "
            + "; ".join(
                f"{PARTIES[label]} {listed} list and {won} electorate" for label, (listed, won) in SEATS_1996.items()
            )
            + "."
        ),
        lang="en",
    )

    t = transcription()
    pages: dict[tuple[str, int], str] = {}

    def page_id(table: str, page: int) -> str:
        if (table, page) not in pages:
            id = pages[(table, page)] = key("page", day, table, page)
            width, height = page_size(artifacts[table].path, page)
            p.add(
                "source_page",
                id=id,
                artifact_id=artifact_ids[table],
                page_number=page,
                image_key=f"pages/{SOURCE}/{artifacts[table].path.stem}/{page}.png",
                width=width,
                height=height,
            )
        return pages[(table, page)]

    def put(unit: str, option: str | None, measure: int, table: str, row: str, column: str) -> None:
        value, page, (x0, y0, x1, y1) = t[(table, row)][column]
        p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=0,
            value=value,
        )
        p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            page_id=page_id(table, page),
            source_id=SOURCE_1996,
            x0=x0,
            y0=y0,
            x1=x1,
            y1=y1,
        )

    for name in electorates():
        code = f"1996:{name}"
        unit = key("unit", "electorate", code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NEW_ZEALAND,
            scheme_code=ec.ELECTORATE_SCHEME,
            code=code,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(unit, name, "en", "official")
        for label, option in options.items():
            put(unit, option, VOTES, "part5", name, label)
        put(unit, None, VALID, "part5", name, "valid")
        put(unit, None, INVALID, "part5", name, "informal")
        put(unit, None, VOTERS, "part9", name, "cast")
        put(unit, None, REGISTERED, "part9", name, "roll")

    nine = {column: cell[0] for column, cell in t[("part9", "Combined Totals")].items()}
    for label, option in options.items():
        p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=combined[label])
    for measure, value in (
        (VALID, combined["valid"]),
        (INVALID, combined["informal"]),
        (VOTERS, nine["cast"]),
        (REGISTERED, nine["roll"]),
    ):
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    return p
