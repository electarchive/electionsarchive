"""Direct elections of Landräte (heads of the Landkreise) in Germany, one Land at a time, with their runoffs.

North Rhine-Westphalia is read from the Landeswahlleiterin's per-Kreis result pages (wahlergebnisse.nrw), Bavaria from
the Landesamt für Statistik's Statistische Berichte on the 2026 municipal elections, and Thuringia from the Landesamt
für Statistik's results database (wahlen.thueringen.de). Each Landkreis is a county polity with its own Landrat
office, and each contest is one Landkreis in one round.
"""

import re
import subprocess
from datetime import date
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

from ea import package, raw
from ea.package import Package
from ea.sources.de import buergermeister as bm

SOURCE = "de.landrat"
LAND = {
    "nw": ("05", "01a13bb5-c563-741b-83f7-4b74611ca2d9", "North Rhine-Westphalia", "Nordrhein-Westfalen"),
    "by": ("09", "01a13bb5-c563-741b-83f7-4b7cdad043bd", "Bavaria", "Bayern"),
    "th": ("16", "01a13bb5-c563-741b-83f7-4b8a6fb78423", "Thuringia", "Thüringen"),
}
SCHEME = "de.landratswahl"
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1ftwo_round"))
KREISE = Path(__file__).with_name("landrat_kreise.tsv")
NW_BASE = "https://www.wahlergebnisse.nrw/kommunalwahlen"
NW_YEARS = (2015, 2020, 2025)
BY_BASE = "https://www.statistik.bayern.de/mam/produkte/veroffentlichungen/statistische_berichte"
BY_FIRST = f"{BY_BASE}/b7331c_202651.pdf"
BY_RUNOFF = f"{BY_BASE}/b7332c_202651.pdf"
TH_BASE = "https://wahlen.thueringen.de/datenbank/wahl1/wahl.asp"
TH_YEARS = (1994, 2000, 2006, 2012, 2018, 2024)
PUBLISHERS = {
    "nw": (
        "Landeswahlleiterin des Landes Nordrhein-Westfalen",
        "Wahlergebnisse in NRW: Kommunalwahlen",
        f"{NW_BASE}/",
        "Datenlizenz Deutschland, Zero, Version 2.0",
        "https://www.govdata.de/dl-de/zero-2-0",
    ),
    "by": (
        "Bayerisches Landesamt für Statistik",
        "Kommunalwahlen in Bayern am 8. März 2026: Ergebnisse der Schnellmeldungen",
        "https://www.statistik.bayern.de/wahlen/kommunalwahlen/bgm/index.html",
        "Creative Commons Namensnennung 4.0 International",
        "https://creativecommons.org/licenses/by/4.0/deed.de",
    ),
    "th": (
        "Thüringer Landesamt für Statistik",
        "Wahlen in Thüringen: Wahl der Landräte und Oberbürgermeister",
        "https://wahlen.thueringen.de/kommunalwahlen/kw_wahlergebnisse.asp",
        "Datenlizenz Deutschland, Namensnennung, Version 2.0",
        "https://www.govdata.de/dl-de/by-2-0",
    ),
}
METHOD = (
    "Counted by the {body} and published for each Landkreis. Registered voters are those entitled to vote, voters are "
    "those who took part, and valid votes are the valid votes for candidates. A candidate with more than half of the "
    "valid votes is elected in the first ballot; otherwise the two leading candidates meet in a runoff, and the one "
    "with more votes is elected. The source prints each candidate's name and party, or only the label of the "
    "nomination."
)
GENERIC = {"einzelbewerber", "einzelbewerberin", "einzelbew.", "ezb", "einzelbewerber/-in", "wählergruppe", "wgr"}


class Kreis(NamedTuple):
    ags: str
    area: str
    name: str


class Round(NamedTuple):
    day: date
    registered: int | None
    voters: int | None
    invalid: int | None
    valid: int | None
    candidates: list[tuple[str, int]]


class Contest(NamedTuple):
    ags: str
    name: str
    rounds: list[Round]
    urls: tuple[str, ...]


def kreise() -> dict[str, Kreis]:
    rows = (line.split("\t") for line in KREISE.read_text().splitlines()[1:])
    return {ags: Kreis(ags, area, name) for ags, area, name in rows}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def clean(name: str) -> str:
    return re.sub(r",\s*(Landkreis|Kreis|Stadt)$", "", name).strip()


def _int(text: str) -> int | None:
    text = text.replace("\xa0", "").replace(" ", "").replace(".", "").strip()
    return int(text) if text.isdigit() else None


def _day(text: str) -> date:
    d, m, y = text.split(".")
    return date(int(y), int(m), int(d))


# North Rhine-Westphalia


def nw_indexes(year: int) -> list[str]:
    return [f"{NW_BASE}/{year}/index_obb_lr.shtml"]


def nw_pages(year: int) -> dict[str, str]:
    """The URL of each Landkreis's page by its five-digit code."""
    text = raw.cached(SOURCE, nw_indexes(year)[0]).path.read_text(errors="replace")
    found: dict[str, str] = {}
    pattern = r'href="(aktuell[\\/](?:b(\d{6})kw\d+\.shtml|(\d{6})_KREIS_XML_DATA\.html))"[^>]*>\s*\d{6}\s+(?!Krfr)'
    for match in re.finditer(pattern, text):
        found["05" + (match.group(2) or match.group(3))[:3]] = f"{NW_BASE}/{year}/" + match.group(1).replace("\\", "/")
    return found


def nw_contests(year: int) -> dict[str, Contest]:
    out: dict[str, Contest] = {}
    for ags, url in sorted(nw_pages(year).items()):
        page = bm.page(url, SOURCE)
        out[ags] = Contest(
            ags,
            page.name,
            [
                Round(r.day, r.registered, r.voters, r.invalid, r.valid, r.candidates)
                for r in page.rounds
                if r.candidates
            ],
            (url,),
        )
    return out


# Bavaria

BY_HEAD = re.compile(r"^(\d{3}) (.+?) \(([^)]+)\)\s*$")
BY_LINE = re.compile(r"^(\S.*?)\s{2,}(?:(Gewählt|Stichwahl)\s+)?(\d[\d.]*|X)\s+([\d,]+|\.)(?:\s+([\d,]+|\.))?\s*$")


def by_text(url: str) -> list[str]:
    path = raw.cached(SOURCE, url).path
    done = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True)
    return done.stdout.splitlines()


def by_section(lines: list[str], title: str) -> list[str]:
    """The lines after the body heading of a section, to the next top-level numbered heading."""
    starts = [i for i, line in enumerate(lines) if line.strip() == title]
    if not starts:
        raise ValueError(f"no section {title}")
    begin = starts[-1]
    end = next(
        (i for i in range(begin + 1, len(lines)) if re.match(r"^\s*\d\. \S", lines[i]) and "Noch" not in lines[i]),
        len(lines),
    )
    return lines[begin:end]


def by_blocks(lines: list[str]) -> dict[str, tuple[str, list[str]]]:
    blocks: dict[str, tuple[str, list[str]]] = {}
    current: list[str] | None = None
    for line in lines:
        head = BY_HEAD.match(line.strip())
        if head:
            current = blocks.setdefault(head.group(1), (head.group(2), []))[1]
        elif current is not None:
            current.append(line)
    return blocks


def by_round(day: date, lines: list[str], runoff: bool) -> Round:
    figures: dict[str, int | None] = {}
    candidates: list[tuple[str, int]] = []
    pending: tuple[str, int] | None = None
    for line in lines:
        flat = line.strip()
        if "valid" in figures:
            break
        for label, name in (
            ("Stimmberechtigte", "registered"),
            ("Wähler", "voters"),
            ("Ungültige Stimmzettel", "invalid"),
            ("Gültige Stimmen insgesamt", "valid"),
            ("Zusammen", "valid"),
        ):
            if flat.startswith(label):
                found = re.search(r"\s(\d[\d.]*)\s", flat + " ")
                figures[name] = _int(found.group(1)) if found else None
                break
        else:
            match = BY_LINE.match(line.strip())
            if match and not flat.startswith(("Bayerisches", "Statistische")):
                if pending is not None and pending[1] >= 0:
                    raise ValueError(f"{pending[0]} has no party line")
                votes = match.group(3)
                pending = (match.group(1).strip(), -1 if votes == "X" else int(votes.replace(".", "")))
            elif pending is not None and flat and not flat.startswith(("Noch", "Gültige", "Zusammen")):
                if pending[1] >= 0:
                    candidates.append((f"{pending[0]} ({flat})", pending[1]))
                pending = None
    valid = figures.get("valid")
    if valid is None and candidates:
        valid = sum(votes for _, votes in candidates)
    return Round(day, figures.get("registered"), figures.get("voters"), figures.get("invalid"), valid, candidates)


# The runoff page of Weißenburg-Gunzenhausen is printed twice over itself in the report's text layer, so the
# page's cells are read by eye from the overlaid lines: 16,397 + 25,563 = 41,960 valid votes, which the printed
# total, and 42,160 voters less 200 invalid, confirm.
BY_REPAIRS = {
    "09577": Round(
        date(2026, 3, 22),
        74672,
        42160,
        200,
        41960,
        [("Westphal, Manuel (CSU)", 16397), ("Gläser, Markus (FREIE WÄHLER)", 25563)],
    )
}


def by_contests() -> dict[str, Contest]:
    first = by_blocks(by_section(by_text(BY_FIRST), "5. Wahl der Landräte in den Landkreisen"))
    second = by_blocks(by_section(by_text(BY_RUNOFF), "6. Stichwahl der Landräte in den Landkreisen"))
    out: dict[str, Contest] = {}
    for code, (name, lines) in sorted(first.items()):
        rounds = [by_round(date(2026, 3, 8), lines, False)]
        urls = (BY_FIRST,)
        if code in second:
            rounds.append(by_round(date(2026, 3, 22), second[code][1], True))
            urls = (BY_FIRST, BY_RUNOFF)
        if "09" + code in BY_REPAIRS:
            rounds.append(BY_REPAIRS["09" + code])
            urls = (BY_FIRST, BY_RUNOFF)
        out["09" + code] = Contest("09" + code, name, rounds, urls)
    return out


# Thuringia


def th_url(year: int, code: str) -> str:
    return f"{TH_BASE}?wahlart=LR&wJahr={year}&zeigeErg=WK&wknr={code}"


def th_codes() -> list[str]:
    return [ags[2:] for ags, kreis in kreise().items() if ags.startswith("16") and "Stadt" not in kreis.name]


def th_page(year: int, code: str) -> Contest | None:
    url = th_url(year, code)
    html = raw.cached(SOURCE, url).path.read_bytes().decode("cp1252", errors="replace")
    html = html[html.find("anderer Landkreis") :]
    text = re.sub(r"<[^>]+>", "\t", re.sub(r"<tr[^>]*>", "\n", html, flags=re.IGNORECASE))
    text = re.sub(r"&nbsp;|&#160;|\xa0", " ", text)
    text = re.sub(r" +", " ", text)
    segments = re.split(r"((?:\d\. Wahlgang|Stichwahl) am \d\d\.\d\d\.\d{4})", text)
    rounds: list[Round] = []
    for index in range(1, len(segments), 2):
        day = _day(segments[index].rsplit(" ", 1)[1])
        body = segments[index + 1]
        flat = " ".join(body.split())

        def figure(label: str, flat: str = flat) -> int | None:
            found = re.search(
                label + r" ([\d ]+?) (?:Wähler|Wahlbeteiligung|Ungültige|Gültige|Nr\.|Eine|\Z)", flat + " "
            )
            return _int(found.group(1)) if found else None

        candidates: list[tuple[str, int]] = []
        for row in body.split("\n"):
            cells = [cell.strip() for cell in row.split("\t") if cell.strip()]
            if len(cells) >= 4 and cells[0].isdigit() and re.fullmatch(r"[\d ]+", cells[2]) and "," in cells[3]:
                candidates.append((cells[1], int(cells[2].replace(" ", ""))))
        if not candidates:
            continue
        invalid = re.search(r"Ungültige Stimmen ([\d ]+?) Gültige", flat)
        valid = re.search(r"Gültige Stimmen ([\d ]+?) (?:Eine|Nr\.)", flat)
        rounds.append(
            Round(
                day,
                figure("Wahlberechtigte"),
                figure("Wähler"),
                _int(invalid.group(1)) if invalid else None,
                _int(valid.group(1)) if valid else None,
                candidates,
            )
        )
    if not rounds:
        return None
    return Contest("16" + code, clean(kreise()["16" + code].name), rounds, (url,))


def th_contests(year: int) -> dict[str, Contest]:
    out: dict[str, Contest] = {}
    for code in th_codes():
        found = th_page(year, code)
        if found:
            out[found.ags] = found
    return out


# Packages


def fetch() -> None:
    for year in NW_YEARS:
        raw.fetch(SOURCE, nw_indexes(year)[0], suffix=".html")
        raw.fetch_many(SOURCE, sorted(nw_pages(year).values()), suffix=".html")
    raw.fetch(SOURCE, BY_FIRST, suffix=".pdf")
    raw.fetch(SOURCE, BY_RUNOFF, suffix=".pdf")
    for year in TH_YEARS:
        raw.fetch_many(SOURCE, [th_url(year, code) for code in th_codes()], suffix=".html")


def label(text: str) -> tuple[str, str | None]:
    found = re.fullmatch(r"(.*?)\s*\(([^()]*)\)", text)
    return (found.group(1), found.group(2)) if found else (text, None)


def party_name(text: str) -> str | None:
    base = text.split("/")[0].strip()
    return None if not base or base.lower() in GENERIC else base


def party_key(land: str, text: str) -> str:
    return key("party", land, text)


def slug(text: str) -> str:
    return bm.slug(text)


def registry(land: str, found: dict[int, dict[str, Contest]]) -> Package:
    _, polity, _, native = LAND[land]
    p = Package(
        f"{SOURCE}/registry-{land}",
        SOURCE,
        f"The Landkreise of {native} as county polities with their Landrat offices, the runoff system, and the "
        "parties and groups that put up Landrat candidates",
    )
    p.external |= {polity, *(kreise()[ags].area for pool in found.values() for ags in pool)}
    publisher, title, url, license_, license_url = PUBLISHERS[land]
    source = key("source", land)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=publisher,
        title=title,
        url=url,
        license=license_,
        license_url=license_url,
        attribution=publisher,
    )
    urls = sorted({item for pool in found.values() for contest in pool.values() for item in contest.urls})
    for item in urls:
        p.artifact(raw.cached(SOURCE, item), source)
    p.add(
        "electoral_system",
        id=SYSTEM if land == "nw" else key("electoral_system", land),
        polity_id=polity,
        valid_from=date(1990, 1, 1),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add("scheme", code=SCHEME, polity_id=polity, kind="electoral_district", rank=1, noun="Landrat electorate")
    names: dict[str, str] = {}
    parties: set[str] = set()
    for pool in found.values():
        for ags, contest in pool.items():
            names[ags] = clean(kreise()[ags].name)
            for round_ in contest.rounds:
                for printed, _ in round_.candidates:
                    base = party_name(label(printed)[1] or (printed if "(" not in printed else ""))
                    if base:
                        parties.add(base)
    for ags, text in sorted(names.items()):
        id = key("polity", ags)
        p.add("polity", id=id, kind="county", iso_code=None)
        p.add("polity_parent", polity_id=id, parent_id=polity)
        p.add("polity_territory", polity_id=id, area_id=kreise()[ags].area)
        p.name(id, kreise()[ags].name, "de", "official")
        p.add("external_id", entity_id=id, scheme="de.ags", value=ags)
        post = key("office", "landrat", ags)
        p.add("office", id=post, polity_id=id, kind="head_of_government")
        p.name(post, f"Landrat of {text}", "en", "official")
        p.name(post, f"Landrat {text}", "de", "native", False)
    for text in sorted(parties):
        id = party_key(land, text)
        p.add("party", id=id, kind="party", polity_id=polity)
        p.add("external_id", entity_id=id, scheme="de.landrat.party", value=f"{land}:{text}")
        p.name(id, text, "de", "official")
    return p


def election(land: str, year: int, pool: dict[str, Contest]) -> Package:
    _, polity, english, native = LAND[land]
    p = Package(
        f"{SOURCE}/{land}-{year}",
        SOURCE,
        f"The {year} Landrat elections in {english}, first ballots and runoffs, by Landkreis",
        depends=[f"{SOURCE}/registry-{land}"],
    )
    p.external |= {polity, *(kreise()[ags].area for ags in pool)}
    source = key("source", land)
    system = SYSTEM if land == "nw" else key("electoral_system", land)
    used: set[str] = set()
    taken: set[tuple[str, str]] = set()
    pools: dict[tuple[int, date], str] = {}
    for ags, contest in sorted(pool.items()):
        for item in contest.urls:
            if item not in used:
                used.add(item)
                p.uses(raw.cached(SOURCE, item))
        area = kreise()[ags].area
        previous: str | None = None
        for index, round_ in enumerate(contest.rounds):
            number = index + 1
            tag = "" if number == 1 else "-runoff"
            path = f"de/{round_.day}-{land}-landrat{tag}"
            if (number, round_.day) not in pools:
                id = pools[(number, round_.day)] = key("election", land, year, number, round_.day)
                p.add(
                    "election",
                    id=id,
                    polity_id=polity,
                    kind="local" if number == 1 else "runoff",
                    date_from=round_.day,
                    date_to=round_.day,
                    status="held",
                )
                p.add("slug", path=path, entity_id=id, is_current=True)
                suffix = "" if number == 1 else " runoff"
                p.name(
                    id,
                    f"{round_.day:%B} {round_.day.day}, {round_.day.year} {english} Landrat elections{suffix}",
                    "en",
                    "official",
                )
                p.name(
                    id,
                    f"Landratswahlen in {native} am {round_.day:%d.%m.%Y}" + ("" if number == 1 else ", Stichwahl"),
                    "de",
                    "native",
                    False,
                )
            election_id = pools[(number, round_.day)]
            contest_id = key("contest", ags, round_.day)
            unit = key("unit", ags, round_.day)
            result_set = key("result_set", ags, round_.day)
            text = clean(kreise()[ags].name)
            p.add(
                "contest",
                id=contest_id,
                election_id=election_id,
                kind="office",
                office_id=key("office", "landrat", ags),
                district_area_id=area,
                magnitude=1,
                round_number=number,
                previous_round_id=previous,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            previous = contest_id
            p.name(
                contest_id,
                f"{round_.day.year} Landrat election in {text}" + ("" if number == 1 else ", runoff"),
                "en",
                "official",
            )
            tail = slug(text)
            if (path, tail) in taken:
                tail = f"{tail}-{ags}"
            taken.add((path, tail))
            p.add("slug", path=f"{path}/{tail}", entity_id=contest_id, is_current=True)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=polity,
                scheme_code=SCHEME,
                code=f"{round_.day}:{ags}",
                area_relation="equals",
                area_id=area,
                kind="standard",
            )
            final = LAND_STAGE[land]
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest_id,
                stage=final,
                kind="counted",
                source_id=source,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            body = {
                "nw": "Landeswahlleiterin of North Rhine-Westphalia",
                "by": "Bavarian State Office for Statistics (its quick reports, Schnellmeldungen, as published in the Statistische Berichte)",
                "th": "Thuringian State Office for Statistics",
            }[land]
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD.format(body=body),
                lang="en",
            )
            for measure, value in ((2, round_.registered), (3, round_.voters), (5, round_.invalid), (4, round_.valid)):
                if value is not None:
                    p.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            top = max(votes for _, votes in round_.candidates)
            last = number == len(contest.rounds)
            total = sum(votes for _, votes in round_.candidates)
            ranked = sorted(round_.candidates, key=lambda item: -item[1])
            for position, (printed, votes) in enumerate(round_.candidates, start=1):
                name, party_label = label(printed)
                named = party_label is not None
                base = party_name(party_label if named and party_label else printed)
                option = key("option", ags, round_.day, position, printed)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest_id,
                    kind="candidate",
                    party_id=party_key(land, base) if base else None,
                    ballot_position=position,
                    ballot_label=printed,
                )
                if named:
                    person = key("person", ags, round_.day, position, printed)
                    p.add("person", id=person)
                    p.name(person, name, "de", "official")
                    p.add(
                        "candidacy",
                        id=key("candidacy", ags, round_.day, position, printed),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=name,
                        is_independent=(party_label or "").lower() in GENERIC,
                    )
                if last:
                    kind = "elected" if votes == top else "lost"
                elif total and top * 2 > total and votes == top:
                    kind = "elected"
                elif (ranked[0][0] == printed or ranked[1][0] == printed) and not top * 2 > total:
                    kind = "advanced"
                else:
                    kind = "lost" if top * 2 > total else "eliminated"
                p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
    return p


LAND_STAGE = {"nw": "official", "by": "preliminary", "th": "official"}


def _all() -> dict[str, dict[int, dict[str, Contest]]]:
    return {
        "nw": {year: nw_contests(year) for year in NW_YEARS},
        "by": {2026: by_contests()},
        "th": {year: pool for year in TH_YEARS if (pool := th_contests(year))},
    }


def packages() -> list[str]:
    keys: list[str] = []
    for land, years in (("nw", NW_YEARS), ("by", (2026,)), ("th", TH_YEARS)):
        keys.append(f"{SOURCE}/registry-{land}")
        keys.extend(f"{SOURCE}/{land}-{year}" for year in years)
    return keys


def parse() -> None:
    for land, found in _all().items():
        registry(land, found).write()
        for year, pool in found.items():
            election(land, year, pool).write()


def emit() -> None:
    package.run("load", packages())
