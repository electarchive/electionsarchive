"""The nine German-speaking municipalities' 2018 council elections, from the German-speaking Community's results site.

The Walloon Region's results site leaves out the nine municipalities of the German-speaking Community (Amel,
Büllingen, Burg-Reuland, Bütgenbach, Eupen, Kelmis, Lontzen, Raeren, Sankt Vith). The Community published their 2018
results on its own site: one page of list votes and seats per municipality, with the registered voters, the ballots
cast, the blank and invalid ballots, and the valid votes, and one page per list with each candidate's preference
votes. The site has no data download, so each municipality's pages were saved as one HTML bundle (every page of the
site's mirror for that municipality, one after the other), and the pipeline reads those bundles from the raw cache.

The municipalities, the municipal council body, and the electoral system are the Walloon pipeline's, because the nine
are Walloon municipalities with Statbel codes, so this package references their ids and adds the election, contests,
parties, and candidacies.
"""

import itertools
import re
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from html import unescape
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.be import wallonie
from ea.sources.be.wallonie import (
    BALLOTS,
    BELGIUM,
    INVALID,
    PREFERENCE,
    REGISTERED,
    VALID,
    Candidate,
    Page,
    Row,
    folded,
    number,
    rows_of,
)

SOURCE = "be.dg"
HOST = "https://ergebnisse.gemeindewahlen2018.be/de"
DAY = date(2018, 10, 14)
UNIT = "be.dg_unit"
MIN_LISTS = 2
# the Community's results site names each municipality by its Statbel code, `CGM<code>`
COMMUNES = {
    "63001": "Amel",
    "63012": "Büllingen",
    "63013": "Bütgenbach",
    "63023": "Eupen",
    "63040": "Kelmis",
    "63048": "Lontzen",
    "63061": "Raeren",
    "63067": "Sankt Vith",
    "63087": "Burg-Reuland",
}
FRENCH = {
    "63001": "Amblève",
    "63012": "Bullange",
    "63013": "Butgenbach",
    "63023": "Eupen",
    "63040": "La Calamine",
    "63048": "Lontzen",
    "63061": "Raeren",
    "63067": "Saint-Vith",
    "63087": "Burg-Reuland",
}
# the saved bundles, by municipality; each is cited under the URL of its first page
PAGES = {
    "63001": f"{HOST}/ergebnisse-zahlen9a69.html?el=CG&id=CGM63001",
    "63012": f"{HOST}/ergebnisse-zahlenf956.html?el=CG&id=CGM63012",
    "63013": f"{HOST}/ergebnisse-zahlen4ebf.html?el=CG&id=CGM63013",
    "63023": f"{HOST}/ergebnisse-zahlen00a0.html?el=CG&id=CGM63023",
    "63040": f"{HOST}/ergebnisse-zahlen9f5d.html?el=CG&id=CGM63040",
    "63048": f"{HOST}/ergebnisse-zahlen7af9.html?el=CG&id=CGM63048",
    "63061": f"{HOST}/ergebnisse-zahlen7527.html?el=CG&id=CGM63061",
    "63067": f"{HOST}/ergebnisse-zahlenfdea.html?el=CG&id=CGM63067",
    "63087": f"{HOST}/ergebnisse-zahlen14fa.html?el=CG&id=CGM63087",
}
INDEX = "https://ergebnisse.gemeindewahlen2018.be/de.html"
TOTALS = {
    "Wahlberechtigte": "registered",
    "Abgegebene Stimmzettel": "cast",
    "Weiß/ungültige Stimmen": "blank",
    "Gültige Stimmen": "valid",
}


@dataclass
class Council:
    code: str
    name: str
    page: Page
    candidates: list[Candidate] = field(default_factory=list)
    heads: dict[str, int] = field(default_factory=dict)  # list number -> votes for the list head
    ballots: dict[str, int] = field(default_factory=dict)  # list number -> ballots with preference votes


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *map(str, parts)])))


SOURCE_ID = key("source", "results")
ELECTION = key("election", DAY)


def party(text: str) -> str:
    return key("party", folded(text))


def sections(html: str) -> list[str]:
    return [part for part in re.split(r"(?=<!DOCTYPE html>)", html) if "<title>" in part]


def seats_of(text: str) -> int | None:
    found = re.match(r"(\d+)\s*\(", text)
    return int(found.group(1)) if found else None


def page_of(code: str, html: str) -> Page:
    table = rows_of(html)
    page = Page(code, COMMUNES[code], [])
    for cell in table:
        # number, list, link, votes 2018, votes 2012, % 2018, % 2012, change, seats
        if len(cell) >= 9 and re.fullmatch(r"\d+", cell[0]) and cell[1] and (votes := number(cell[3])) is not None:
            page.rows.append(Row(cell[0], cell[1], votes, seats_of(cell[-1])))
    for first, second in itertools.pairwise(table):
        if first and first[0] in TOTALS and second and second[0] == "Total":
            setattr(page, TOTALS[first[0]], number(second[1]))
    return page


def preferences(html: str) -> tuple[list[Candidate], str, int | None, int | None]:
    found: list[Candidate] = []
    list_number, label, head, ballots = "", "", None, None
    for cell in rows_of(html):
        if len(cell) == 1 and (title := re.fullmatch(r"(\d+)\s+(.+)", cell[0])):
            list_number, label = title.group(1), title.group(2)
        elif (
            len(cell) == 9 and re.fullmatch(r"\d+", cell[0]) and list_number and (votes := number(cell[2])) is not None
        ):
            found.append(Candidate(list_number, label, int(cell[0]), cell[1], votes, bool(cell[7])))
        elif len(cell) == 3 and cell[0] == "B":
            head = number(cell[2])
        elif len(cell) == 3 and cell[0] == "C":
            ballots = number(cell[2])
    return found, list_number, head, ballots


def load() -> list[Council]:
    found = []
    for code, name in COMMUNES.items():
        bundle = raw.cached(SOURCE, PAGES[code]).path.read_bytes().decode("utf-8")
        parts = sections(bundle)
        results = next(part for part in parts if "<title>Ergebnisse in Zahlen" in part)
        header = re.search(r'article-dataheader__title">Gemeinde ([^<]*)<', results)
        if header is None or unescape(header.group(1)) != name:
            raise ValueError(f"CGM{code} is not {name}")
        council = Council(code, name, page_of(code, results))
        for part in parts:
            if "<title>Vorzugsstimmen" not in part:
                continue
            candidates, list_number, head, ballots = preferences(part)
            council.candidates += candidates
            if head is not None:
                council.heads[list_number] = head
            if ballots is not None:
                council.ballots[list_number] = ballots
        found.append(council)
    return found


def known_parties(councils: list[Council]) -> dict[str, str]:
    seen: dict[str, Counter[str]] = {}
    counted: Counter[str] = Counter()
    for council in councils:
        for row in council.page.rows:
            seen.setdefault(folded(row.label), Counter())[row.label] += 1
            counted[folded(row.label)] += 1
    return {f: labels.most_common(1)[0][0] for f, labels in seen.items() if f and counted[f] >= MIN_LISTS}


METHOD = (
    "Published by the German-speaking Community on its results site for the municipal council elections of "
    "14 October 2018: list votes and seats per municipality, with the registered voters, the ballots cast, the "
    "blank and invalid ballots, and the valid votes, and each candidate's preference votes. The site prints blank "
    "and invalid ballots as one figure, which is stored as invalid ballots. The site labels its figures provisional "
    "until the validation of the results. Seats and elected candidates are as the site declares them; the archive "
    "stores them and does not recompute them. Valid votes are the votes for lists. Each candidate's votes are the "
    "personal votes the page prints, before the transfer of list-head votes."
)


def election() -> Package:
    councils = load()
    p = Package(
        f"{SOURCE}/{DAY}",
        SOURCE,
        "The 2018 council elections of the nine German-speaking municipalities from the Community's results site",
        depends=[f"{wallonie.SOURCE}/geo", f"{wallonie.SOURCE}/registry"],
    )
    p.external.add(BELGIUM)
    for code in COMMUNES:
        p.uses(raw.cached(SOURCE, PAGES[code]))
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Deutschsprachige Gemeinschaft",
        title="Gemeinderatswahlen 2018: Ergebnisse",
        url="https://ergebnisse.gemeindewahlen2018.be/de.html",
        attribution="Deutschsprachige Gemeinschaft Belgiens",
    )
    for url in [*PAGES.values(), INDEX]:
        p.artifact(raw.cached(SOURCE, url), SOURCE_ID)
    p.add(
        "scheme",
        code=UNIT,
        polity_id=BELGIUM,
        kind="electoral_district",
        rank=3,
        noun="reporting unit",
        code_system=None,
    )
    base = f"be/{DAY}-german-speaking-local"
    p.add("election", id=ELECTION, polity_id=BELGIUM, kind="local", date_from=DAY, date_to=DAY, status="held")
    p.name(ELECTION, "2018 municipal elections in the German-speaking Community", "en", "official")
    p.name(ELECTION, "Gemeinderatswahlen 2018 in der Deutschsprachigen Gemeinschaft", "de", "native", False)
    p.add("slug", path=base, entity_id=ELECTION, is_current=True)
    known = known_parties(councils)
    for text in sorted(known.values()):
        p.add("party", id=party(text), kind="party", polity_id=BELGIUM)
        p.name(party(text), text, "und", "official")
    for council in councils:
        town = wallonie.area(wallonie.MUNICIPALITY, council.code)
        tag = f"CGM{council.code}"
        c = key("contest", DAY, tag)
        seats = sum(row.seats or 0 for row in council.page.rows)
        p.add(
            "contest",
            id=c,
            election_id=ELECTION,
            kind="list_allocation",
            body_id=wallonie.MUNICIPAL_BODY,
            district_area_id=town,
            magnitude=seats,
            round_number=1,
            electoral_system_id=wallonie.SYSTEM,
            default_result_kind="counted",
        )
        p.name(c, f"{council.name} municipal council", "en", "official")
        p.name(c, f"Gemeinderat {council.name}", "de", "native", False)
        p.name(c, f"Conseil communal de {FRENCH[council.code]}", "fr", "native", False)
        p.add(
            "slug",
            path=f"{base}/municipal-councils/{changeset.slug(council.name)}",
            entity_id=c,
            is_current=True,
        )
        result_set = key("result_set", DAY, tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=ELECTION,
            contest_id=c,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        unit = key("unit", DAY, tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=BELGIUM,
            scheme_code=UNIT,
            code=f"{DAY}:{tag}",
            area_relation="equals",
            area_id=town,
            kind="standard",
        )
        for attribute, measure in (("registered", REGISTERED), ("cast", BALLOTS), ("blank", INVALID), ("valid", VALID)):
            value = getattr(council.page, attribute)
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
        options: dict[str, str] = {}
        for row in council.page.rows:
            option = options[row.number or ""] = key("option", DAY, tag, row.number)
            fold = folded(row.label)
            p.add(
                "contest_option",
                id=option,
                contest_id=c,
                kind="list",
                party_id=party(known[fold]) if fold in known else None,
                ballot_position=int(row.number) if row.number else None,
                ballot_label=row.label,
            )
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=row.votes,
            )
            if row.seats:
                p.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=row.seats)
        for cand in council.candidates:
            ident = f"{DAY}:{council.code}:{cand.number}:{cand.position}"
            person = key("person", ident)
            p.add("person", id=person, birth_precision=None)
            p.name(person, cand.name, "de", "official")
            candidacy = key("candidacy", ident)
            list_option = options[cand.number]
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=list_option,
                role="list_member",
                list_position=cand.position,
                ballot_name=cand.name,
            )
            sub = key("option", DAY, tag, cand.number, cand.position, "candidate")
            p.add(
                "contest_option",
                id=sub,
                contest_id=c,
                kind="candidate",
                parent_option_id=list_option,
                ballot_position=cand.position,
                ballot_label=cand.name,
            )
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=sub,
                channel=0,
                measure=PREFERENCE,
                count_round=0,
                flags=0,
                value=cand.votes,
            )
            if cand.elected:
                p.add(
                    "outcome", result_set_id=result_set, option_id=list_option, candidacy_id=candidacy, kind="elected"
                )
    return p


def verify(councils: list[Council]) -> None:
    """Fail when a page's own figures disagree: lists against valid votes, list votes against their head and
    preference ballots, and elected candidates against seats."""
    for council in councils:
        page = council.page
        if sum(row.votes for row in page.rows) != page.valid:
            raise ValueError(f"{council.name}: list votes do not sum to the valid votes")
        if (page.valid or 0) + (page.blank or 0) != page.cast:
            raise ValueError(f"{council.name}: valid and blank votes do not sum to the ballots cast")
        for row in page.rows:
            cands = [c for c in council.candidates if c.number == row.number]
            if row.votes != council.heads.get(row.number or "", 0) + council.ballots.get(row.number or "", 0):
                raise ValueError(f"{council.name} list {row.label}: head and preference ballots differ from list votes")
            if sum(c.elected for c in cands) != (row.seats or 0):
                raise ValueError(f"{council.name} list {row.label}: elected candidates differ from seats")


def fetch() -> None:
    for url in [*PAGES.values(), INDEX]:
        raw.fetch(SOURCE, url, suffix=".html")


def packages() -> list[str]:
    return [f"{SOURCE}/{DAY}"]


def parse() -> None:
    verify(load())
    election().write()


def emit() -> None:
    package.run("load", packages())
