"""Hong Kong Election Committee subsector elections published by the Registration and Electoral Office.

One contest per subsector (or sub-subsector) of the Election Committee, for the 2016 and 2021 ordinary elections and
the 2025 by-elections. A subsector whose candidates equal its seats is returned uncontested, which the archive stores
as a tacit result set. The 2026 ordinary election is held on 22 November 2026; its results page is still empty.
"""

import json
import re
from dataclasses import dataclass
from datetime import date
from html import unescape
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "hk.ecss"
HOST = "https://www.elections.gov.hk"
HONG_KONG = str(uuid5(package.NAMESPACE, "polity\x1fHK"))
VOTES = 1
PAGE = f"{HOST}/index.html"

SITES = {
    date(2016, 12, 11): ("ecss2016", 4),
    date(2021, 9, 19): ("ecss2021", 5),
    date(2025, 9, 7): ("ecss2025by", 5),
}
NAMES = {
    date(2016, 12, 11): ("2016 Election Committee subsector ordinary elections", "2016年選舉委員會界別分組選舉"),
    date(2021, 9, 19): ("2021 Election Committee subsector ordinary elections", "2021年選舉委員會界別分組一般選舉"),
    date(2025, 9, 7): ("2025 Election Committee subsector by-elections", "2025年選舉委員會界別分組補選"),
}
SMALL = {"and", "of", "the", "for", "to", "in"}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


ECSS_SOURCE = key("source", "reo")
COMMITTEE = key("body", "election-committee")
FPTP = key("electoral_system", "fptp")
BLOCK = key("electoral_system", "block")
SCHEME = "hk.subsector"

METHOD = (
    "Votes are those the Registration and Electoral Office published for each candidate of the subsector. Each "
    "elector votes for up to as many candidates as the subsector has seats. The office publishes votes received and "
    "whether the candidate was elected; it does not publish the number of electors, ballots cast, or valid votes "
    "in these tables."
)
TACIT = (
    "The number of candidates validly nominated did not exceed the number of seats, so the candidates were "
    "returned uncontested and no poll was held."
)
LOTS = "A tie for the last seat was decided by drawing lots, as the office notes beside the result."


def page_urls(day: date) -> list[str]:
    site, count = SITES[day]
    base = f"{HOST}/{site}/eng"
    first = f"{base}/results.html" if day.year == 2016 else f"{base}/results_s1.html"
    return [first, *(f"{base}/results_s{n}.html" for n in range(2, count + 1))]


def _get(url: str, suffix: str = ".html") -> raw.Artifact:
    return raw.fetch(SOURCE, url, suffix=suffix)


def _own(url: str) -> raw.Artifact:
    return raw.cached(SOURCE, url)


def clean(text: object) -> str:
    return " ".join(unescape(str(text or "")).replace("\xa0", " ").split())


def strip(html: str) -> str:
    return clean(re.sub(r"(?s)<[^>]+>", " ", html))


def page(url: str) -> str:
    return re.sub(r"(?s)<!--.*?-->", "", _own(url).path.read_text(encoding="utf-8-sig"))


def script_urls(day: date, html: str) -> list[str]:
    """The result scripts a 2025 page loads for its contested subsectors."""
    site = SITES[day][0]
    return [
        f"{HOST}/{site}/js/election.results/{name}"
        for name in re.findall(r"election\.results/(rs\.[a-z0-9]+\.js)", html)
    ]


def fetch() -> None:
    for day in SITES:
        for url in page_urls(day):
            artifact = _get(url)
            if day.year == 2025:
                for js in script_urls(day, artifact.path.read_text(encoding="utf-8-sig")):
                    _get(js, ".js")


def all_urls() -> list[str]:
    urls: list[str] = []
    for day in SITES:
        for url in page_urls(day):
            urls.append(url)
            if day.year == 2025:
                urls += script_urls(day, page(url))
    return urls


@dataclass
class Candidate:
    number: str
    name: str
    votes: int | None
    elected: bool = False
    lots: bool = False


@dataclass
class Subsector:
    name: str
    candidates: list[Candidate]
    contested: bool
    lots: bool = False
    code: str = ""

    @property
    def seats(self) -> int:
        return sum(c.elected for c in self.candidates) if self.contested else len(self.candidates)


def title(text: str) -> str:
    words = clean(text).replace("'S ", "'s ").split(" ")
    return " ".join(w.lower() if i and w.lower() in SMALL else w for i, w in enumerate(words))


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def cells(row: str) -> list[str]:
    return [strip(cell) for cell in re.findall(r"(?is)<td\b[^>]*>(.*?)</td>", row)]


def reading(text: str) -> tuple[int | None, bool, bool]:
    """Votes, elected, and drawn by lot from a published vote cell."""
    found = re.fullmatch(r"([\d,]+)\s*(\(Elected\))?\s*(#)?", text)
    if not found:
        return None, False, False
    return int(found.group(1).replace(",", "")), bool(found.group(2)), bool(found.group(3))


def tables_2016(html: str) -> list[tuple[str, list[list[str]]]]:
    out = []
    for block in re.split(r"(?is)<caption\b[^>]*>", html)[1:]:
        name, _, rest = block.partition("</caption>")
        rows = [cells(r) for r in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", rest.split("</table>")[0])]
        out.append((strip(name), [r for r in rows if len(r) == 3 and not r[2].startswith("Number")]))
    return out


def tables_2021(html: str) -> list[tuple[str, list[list[str]]]]:
    out = []
    for name, body in re.findall(r'(?is)<h2 ?>([^<]*)</h2>\s*<table class="vt_result">(.*?)</table>', html):
        rows = [cells(r) for r in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", body)]
        out.append((strip(name), [r for r in rows if len(r) == 3]))
    return out


def tables_2025(html: str) -> list[tuple[str, list[list[str]]]]:
    out = []
    for body in re.findall(r"(?is)<table>(.*?)</table>", html):
        found = re.search(r'(?is)<th colspan="3"><span>(.*?)</span>', body)
        if not found:
            continue
        rows = []
        for r in re.findall(r"(?is)<tr\b[^>]*>(.*?)</tr>", body):
            row = cells(r)
            ident = re.search(r'id="votes_obtained_([A-Z0-9]+)_(\d*)"', r)
            if len(row) == 3 and ident:
                rows.append([*row, ident.group(1)])
        out.append((strip(found.group(1)), rows))
    return out


def script_results(day: date, html: str) -> dict[str, dict[str, tuple[str, bool, bool]]]:
    """Votes, ordinary-election flag, and drawn-by-lot flag per candidate number, per subsector code."""
    found: dict[str, dict[str, tuple[str, bool, bool]]] = {}
    for url in script_urls(day, html):
        text = _own(url).path.read_text(encoding="utf-8-sig")
        for code, body in re.findall(
            r'"subsectorCode"\s*:\s*"(\w+)"\s*,\s*"resultsLists"\s*:\s*(\[.*?\])\s*}', text, re.DOTALL
        ):
            body = re.sub(r",\s*]", "]", body)
            rows = json.loads(body)
            found[code] = {
                str(r["cand_number"]): (
                    str(r.get("votes_obtained", "")),
                    r.get("ordinary") == "Y",
                    r.get("drawlot") == "Y",
                )
                for r in rows
            }
    return found


def parse_page(day: date, url: str) -> list[Subsector]:
    html = page(url)
    result: list[Subsector] = []
    if day.year == 2016:
        tables = tables_2016(html)
    elif day.year == 2021:
        tables = tables_2021(html)
    else:
        tables = tables_2025(html)
        scripts = script_results(day, html)
    for name, rows in tables:
        if not rows:
            raise ValueError(f"{day} {name}: no candidates")
        contested = not all("UNCONTESTED" in r[2] for r in rows)
        if not contested and any("UNCONTESTED" not in r[2] for r in rows):
            raise ValueError(f"{day} {name}: mixed contested and uncontested rows")
        sub = Subsector(title(name), [], contested)
        for index, row in enumerate(rows, 1):
            number = row[0] if contested else ""
            votes: int | None = None
            elected = lots = False
            if not contested:
                elected = True
            elif day.year == 2025:
                sub.code = row[3]
                text, ordinary, drawn = scripts[row[3]][number]
                votes, _, _ = reading(text)
                elected, lots = ordinary, ordinary and drawn
            else:
                votes, elected, lots = reading(row[2])
                if votes is None:
                    raise ValueError(f"{day} {name} {row[1]}: unreadable votes {row[2]!r}")
            sub.candidates.append(Candidate(number or str(index), row[1], votes, elected, lots))
        sub.lots = any(c.lots for c in sub.candidates)
        if contested and not any(c.elected for c in sub.candidates):
            raise ValueError(f"{day} {name}: contested with no one elected")
        result.append(sub)
    return result


def read(day: date) -> list[Subsector]:
    found = [s for url in page_urls(day) for s in parse_page(day, url)]
    names = [s.name for s in found]
    if len(set(names)) != len(names):
        raise ValueError(f"{day}: repeated subsector names")
    return found


def day_key(day: date) -> str:
    return day.isoformat()


def packages() -> list[str]:
    return ["hk.ecss/registry", *(f"hk.ecss/{day_key(d)}" for d in SITES)]


def parse() -> None:
    registry().write()
    for day in SITES:
        build(day).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        "hk.ecss/registry",
        SOURCE,
        "Hong Kong's Election Committee and the unit scheme its subsector election results use, from the "
        "Registration and Electoral Office",
    )
    p.external.add(HONG_KONG)
    p.add(
        "source",
        id=ECSS_SOURCE,
        kind="official_body",
        publisher="Registration and Electoral Office, Hong Kong",
        title="Election Committee subsector election results published by the Registration and Electoral Office",
        url=PAGE,
        license="Official publication; reuse terms are not stated",
        attribution="Registration and Electoral Office, Hong Kong Special Administrative Region Government",
    )
    for url in all_urls():
        p.artifact(_own(url), ECSS_SOURCE)
    p.add("scheme", code=SCHEME, polity_id=HONG_KONG, kind="electoral_district", rank=1, noun="subsector")
    p.add("body", id=COMMITTEE, polity_id=HONG_KONG, kind="electoral_college")
    p.name(COMMITTEE, "Election Committee of Hong Kong", "en", "official")
    p.name(COMMITTEE, "選舉委員會", "zh", "native", False)
    systems = (
        (FPTP, {"formula": "fptp"}, "The candidate with the most votes wins the single seat."),
        (
            BLOCK,
            {"formula": "block"},
            (
                "Each elector votes for up to as many candidates as there are seats, and the candidates with the most "
                "votes win."
            ),
        ),
    )
    for id, rules, text in systems:
        p.add("electoral_system", id=id, polity_id=HONG_KONG, rules=rules, rules_version=1)
        p.add("note", id=key("note", id), entity_id=id, kind="methodology", body_md=text, lang="en")
    return p


def build(day: date) -> Package:
    name = day_key(day)
    p = Package(
        f"hk.ecss/{name}",
        SOURCE,
        f"Hong Kong Election Committee subsector elections of {day}, one contest per subsector",
        depends=["hk.ecss/registry"],
    )
    p.external.add(HONG_KONG)
    for url in page_urls(day):
        p.uses(_own(url))
        if day.year == 2025:
            for js in script_urls(day, page(url)):
                p.uses(_own(js))
    en, zh = NAMES[day]
    election = key("election", name)
    p.add(
        "election",
        id=election,
        polity_id=HONG_KONG,
        kind="indirect",
        date_from=day,
        date_to=day,
        status="held",
    )
    p.add("slug", path=f"hk/{name}-election-committee-subsectors", entity_id=election, is_current=True)
    p.name(election, en, "en", "official")
    p.name(election, zh, "zh", "native", False)
    subsectors = read(day)
    for sub in subsectors:
        _contest(p, name, election, en, sub)
    _accept(p, day, subsectors)
    return p


def _contest(p: Package, name: str, election: str, en: str, sub: Subsector) -> None:
    code = slug(sub.name)
    seats = sub.seats
    contest = key("contest", name, code)
    system = FPTP if seats == 1 else BLOCK
    kind = "counted" if sub.contested else "tacit"
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=COMMITTEE,
        magnitude=seats,
        round_number=1,
        electoral_system_id=system,
        default_result_kind=kind,
    )
    p.name(contest, f"{en.removesuffix('s')}, {sub.name} subsector", "en", "official")
    p.add("slug", path=f"hk/{name}-election-committee-subsectors/{code}", entity_id=contest, is_current=True)
    result_set = key("result_set", name, code)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind=kind,
        source_id=ECSS_SOURCE,
        status="active",
        is_default=True,
        completeness=1.0 if sub.contested else 0.0,
    )
    note = METHOD if sub.contested else TACIT
    if sub.lots:
        note += " " + LOTS
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=note, lang="en")
    unit = None
    if sub.contested:
        unit = key("unit", name, code)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=HONG_KONG,
            scheme_code=SCHEME,
            code=f"{name}:{code}",
            area_relation="unlocated",
            area_id=None,
            kind="aggregate",
        )
        p.name(unit, sub.name, "en", "official")
    for candidate in sub.candidates:
        tag = f"{code}:{candidate.number}"
        person = key("person", name, tag)
        option = key("option", name, tag)
        p.add("person", id=person)
        p.name(person, candidate.name, "en", "official")
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=int(candidate.number) if sub.contested else None,
            ballot_label=candidate.name,
        )
        p.add(
            "candidacy",
            id=key("candidacy", name, tag),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=candidate.name,
        )
        if candidate.elected:
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        if sub.contested:
            p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=candidate.votes)
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=candidate.votes,
            )


def _accept(p: Package, day: date, subsectors: list[Subsector]) -> None:
    del p, day, subsectors
