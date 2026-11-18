"""Tweede Kamer district elections 1848-1917, from the Kiesraad's Databank Verkiezingsuitslagen (CC0).

The databank files the district elections under 33 entries, one for each general election or renewal, and each entry
holds every polling day until the next one: the general polling day, its runoffs (herstemmingen), and the
by-elections (tussentijdse verkiezingen) between renewals with their runoffs. A detail page embeds the entry and lists
its polling days; each polling day's JSON names the districts that voted with their electorate, turnout, and ballots;
each district's JSON gives its candidates' votes and, for many later polls, the votes in each municipality
(stemdistrict) of the district; and a CSV per polling day gives each district's code. Each district's history in an
entry states whether a polling day was a general election, a runoff, or a by-election.
"""

import csv
import functools
import html
import io
import json
import os
import re
import ssl
import sys
from datetime import date, datetime
from typing import Any, NamedTuple
from uuid import uuid5

import certifi
import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "nl.databank_1848"
HOST = "https://www.verkiezingsuitslagen.nl"
# The host serves its leaf certificate without the intermediate that signed it.
INTERMEDIATE = "http://crt.sectigo.com/SectigoPublicServerAuthenticationCADVR36.crt"
CODES = [
    "TK18481130",
    "TK18500827",
    "TK18520608",
    "TK18530517",
    "TK18540613",
    "TK18560610",
    "TK18580608",
    "TK18600612",
    "TK18620610",
    "TK18640614",
    "TK18660612",
    "TK18661030",
    "TK18680122",
    "TK18690608",
    "TK18710613",
    "TK18730610",
    "TK18750608",
    "TK18770612",
    "TK18790610",
    "TK18810614",
    "TK18830612",
    "TK18841028",
    "TK18860615",
    "TK18870901",
    "TK18880306",
    "TK18910609",
    "TK18940410",
    "TK18970615",
    "TK19010614",
    "TK19050616",
    "TK19090611",
    "TK19130617",
    "TK19170615",
]


def page_url(code: str) -> str:
    return f"{HOST}/verkiezingen/detail/{code}"


def stemming_url(code: str, option: int) -> str:
    return f"{HOST}/verkiezingen/detailJson/{code}/{option}"


def csv_url(code: str, stemming: int) -> str:
    return f"{HOST}/uitslag/verkiezing/{code}/voor1918/{stemming}"


def district_url(region: int) -> str:
    return f"{HOST}/verkiezingen/KiesdistrictStemregioUitslagDataJson/?stemregioId={region}"


def history_url(election: int, region: int) -> str:
    return f"{HOST}/verkiezingen/VerkiezingKiesdistrictDataJson/?verkiezingId={election}&regioId={region}"


@functools.cache
def context() -> ssl.SSLContext:
    """The platform's trusted roots plus the Sectigo intermediate the host leaves out of its chain."""
    artifact = raw.fetch(SOURCE, INTERMEDIATE, suffix=".crt")
    ctx = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
    ctx.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(artifact.path.read_bytes()))
    return ctx


@functools.cache
def page(code: str) -> dict[str, Any]:
    text = raw.cached(SOURCE, page_url(code)).path.read_text(encoding="utf-8")
    found = re.search(r'<textarea id="UitslagData"[^>]*>(.*?)</textarea>', text, re.DOTALL)
    assert found is not None, f"{code}: the page embeds no result"
    return json.loads(html.unescape(found.group(1)))


def options(code: str) -> list[int]:
    """The election's polling days (the page's Stemming options), each named by its national region's id."""
    return [int(option["Id"]) for option in page(code)["Regios"]["Regios"][0]["Options"]]


def districts(code: str) -> range:
    """The databank's ids of the 119 districts of 1848-1917, which key each district's polling days in an election.
    A page's district selection lists only the 68 districts of 1848; the others answer at the ids between them."""
    return range(32658, 32777)


@functools.cache
def stemming(code: str, option: int) -> dict[str, Any]:
    return json.loads(raw.cached(SOURCE, stemming_url(code, option)).path.read_text(encoding="utf-8"))


def fetch() -> None:
    ctx = context()
    for code in CODES:
        raw.fetch(SOURCE, page_url(code), suffix=".html", verify=ctx)
    raw.fetch_many(
        SOURCE,
        [stemming_url(code, option) for code in CODES for option in options(code)],
        suffix=".json",
        verify=ctx,
        workers=8,
    )
    raw.fetch_many(
        SOURCE,
        [csv_url(code, stemming(code, option)["Info"]["StemmingId"]) for code in CODES for option in options(code)],
        suffix=".csv",
        verify=ctx,
        workers=8,
    )
    raw.fetch_many(
        SOURCE,
        [history_url(page(code)["Info"]["Id"], region) for code in CODES for region in districts(code)],
        suffix=".json",
        verify=ctx,
        workers=8,
    )
    regions = [
        int(sub["StemregioId"])
        for code in CODES
        for option in options(code)
        for sub in stemming(code, option)["Stemregio"]["SubStemregios"] or []
    ]
    raw.fetch_many(SOURCE, [district_url(region) for region in regions], suffix=".json", verify=ctx, workers=8)


NETHERLANDS = str(uuid5(package.NAMESPACE, "polity\x1fNL"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNLD"))
DISTRICTS = "nl.kiesdistrict_1848"
MUNICIPAL = "nl.stemdistrict_1848"
VOTES, REGISTERED, BALLOTS, VALID, INVALID, BLANK, VOTERS = 1, 2, 3, 4, 5, 6, 14
FIGURES = {
    "Kiesgerechtigden": REGISTERED,
    "Opkomst": VOTERS,
    "AantalStembriefjes": BALLOTS,
    "AantalGeldigeStemmen": VALID,
    "AantalOngeldigeStemmen": INVALID,
    "AantalBlancoStemmen": BLANK,
}
MONTHS = [
    "januari",
    "februari",
    "maart",
    "april",
    "mei",
    "juni",
    "juli",
    "augustus",
    "september",
    "oktober",
    "november",
    "december",
]
METHOD = (
    "Each candidate's votes in the district, with the district's electorate (kiesgerechtigden), voters (opkomst), "
    "ballot papers (stembriefjes), valid votes, and blank ballots, as the Kiesraad's Databank Verkiezingsuitslagen "
    "publishes them (CC0) from the collection of the Instituut voor Nederlandse Geschiedenis. Until 1918 members were "
    "chosen in districts by absolute majority, with a runoff (herstemming) when nobody won one; some districts "
    "elected two or more members, and a voter then had as many votes as seats, so candidate votes can add up to more "
    "than the valid ballots. The databank lists the candidates it has figures for, so votes scattered over others "
    "are missing and candidate votes can add up to less. It marks no candidate as elected, so no outcome is stored, "
    "and it does not state how many seats a poll filled."
)
FINE_METHOD = (
    "The votes in each municipality (stemdistrict) of the district, as the Kiesraad's Databank Verkiezingsuitslagen "
    "publishes them (CC0). For many polls the databank gives a municipality's valid votes and ballot papers but not "
    "its candidates' votes, so this set's completeness is the share of the district's candidate votes that its "
    "municipalities account for. The district totals are the default result."
)
TOTALS_REPORT: dict[str, str] = {}
LEFT_OUT: list[str] = []


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


KIESRAAD = key("source", "kiesraad.databank")
BODY = key("body", "TK", "NL")


def area(district: str) -> str:
    return key("area", DISTRICTS, district)


def unit(district: str) -> str:
    return key("unit", DISTRICTS, district)


def sub_code(district: str, name: str) -> str:
    return f"{district}:{changeset.slug(name)}"


def sub_unit(district: str, name: str) -> str:
    return key("unit", MUNICIPAL, sub_code(district, name))


def person(name: str) -> str:
    return key("person", name)


def entry_day(code: str) -> date:
    return datetime.strptime(code[2:], "%Y%m%d").date()


def dutch(day: date) -> str:
    return f"{day.day} {MONTHS[day.month - 1]} {day.year}"


def whole(text: object) -> int | None:
    digits = re.sub(r"\D", "", str(text or ""))
    return int(digits) if digits else None


class Poll(NamedTuple):
    """One district's vote on one polling day."""

    entry: str
    day: date
    stemming: int
    region: int
    district: str
    name: str
    kind: str
    figures: dict[int, int]
    candidates: list[tuple[str, int, str | None]]
    municipalities: list[dict[str, Any]]


class Chain(NamedTuple):
    root: Poll
    round: int
    previous: Poll | None


def history(code: str) -> dict[int, str]:
    """The polling day's kind (StemmingType) for each district region, from the entry's district histories."""
    out: dict[int, str] = {}
    election = page(code)["Info"]["Id"]
    for region in districts(code):
        for row in json.loads(raw.cached(SOURCE, history_url(election, region)).path.read_text())["Stemregios"]:
            out[int(row["StemregioId"])] = str(row["StemmingType"])
    return out


def district_codes(code: str, stemming: int) -> dict[str, str]:
    text = raw.cached(SOURCE, csv_url(code, stemming)).path.read_text(encoding="utf-8")
    rows = list(csv.reader(io.StringIO(text), delimiter=";"))
    assert rows[0][:5] == ["Regio", "Code", "Uitslag", "Kandidaat", "AantalStemmen"], rows[0]
    out: dict[str, str] = {}
    for row in rows[1:]:
        if len(row) >= 2 and row[1]:
            assert out.setdefault(row[0], row[1]) == row[1]
    return out


def district_json(region: int) -> dict[str, Any]:
    return json.loads(raw.cached(SOURCE, district_url(region)).path.read_text(encoding="utf-8"))


@functools.cache
def polls() -> list[Poll]:
    out: list[Poll] = []
    for code in CODES:
        kinds = history(code)
        for option in options(code):
            data = stemming(code, option)
            day = datetime.strptime(data["Info"]["DateStemming"], "%d-%m-%Y").date()
            number = int(data["Info"]["StemmingId"])
            codes = district_codes(code, number)
            for sub in data["Stemregio"]["SubStemregios"] or []:
                name = " ".join(str(sub["Naam"]).split())
                region = int(sub["StemregioId"])
                detail = district_json(region)
                if not codes.get(name, "").startswith("KD") or not detail["KandidaatUitslag"]:
                    assert not detail["KandidaatUitslag"], (code, name)
                    LEFT_OUT.append(f"{name} on {day} ({code}, {codes.get(name, 'no code')})")
                    continue
                figures = {measure: whole(sub[field]) for field, measure in FIGURES.items()}
                candidates = [
                    (" ".join(str(row["Naam"]).split()), whole(row["AantalStemmen"]), row["Aanbeveling"])
                    for row in detail["KandidaatUitslag"]
                ]
                assert all(votes is not None for _, votes, _ in candidates), (code, name)
                assert len({text for text, _, _ in candidates}) == len(candidates), (code, name)
                out.append(
                    Poll(
                        entry=code,
                        day=day,
                        stemming=number,
                        region=region,
                        district=codes[name],
                        name=name,
                        kind=kinds[region],
                        figures={measure: value for measure, value in figures.items() if value is not None},
                        candidates=[(text, votes or 0, recommended) for text, votes, recommended in candidates],
                        municipalities=detail["Stemdistricten"] or [],
                    )
                )
    return sorted(out, key=lambda poll: (poll.day, poll.district, poll.stemming))


def second_seat(poll: Poll) -> bool:
    return "2e zetel" in poll.kind


@functools.cache
def chains() -> dict[int, Chain]:
    """Each poll's first round, its round number, and the poll it is the runoff of."""
    out: dict[int, Chain] = {}
    last: dict[tuple[str, bool], Poll] = {}
    for poll in polls():
        if poll.kind.startswith("herstemming"):
            previous = last.get((poll.district, second_seat(poll))) or last[(poll.district, not second_seat(poll))]
            before = out[previous.region]
            out[poll.region] = Chain(before.root, before.round + 1, previous)
        else:
            assert poll.kind.startswith(("algemene stemming", "tussentijdse stemming")), poll.kind
            out[poll.region] = Chain(poll, 1, None)
        last[(poll.district, second_seat(poll))] = poll
    return out


def general(poll: Poll) -> bool:
    return poll.kind.startswith("algemene")


def group(poll: Poll) -> tuple[str, str, date]:
    """The election a poll belongs to: its entry's general election, or the by-elections of its first round's day."""
    root = chains()[poll.region].root
    return (
        root.entry,
        "general" if general(root) else "by_election",
        entry_day(root.entry) if general(root) else root.day,
    )


def package_key(code: str) -> str:
    return f"{SOURCE}/{entry_day(code)}"


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(package_key(code) for code in CODES)]


def parse() -> None:
    registry().write()
    for code in CODES:
        build(code).write()
    for day, line in TOTALS_REPORT.items():
        print(day, line)
    for line in sorted(set(LEFT_OUT)):
        print("left out:", line, file=sys.stderr)


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Districts, municipalities, and candidates of the Tweede Kamer district elections 1848-1917 from the "
        "Kiesraad's databank",
    )
    p.external |= {NETHERLANDS, COUNTRY}
    p.add(
        "source",
        id=KIESRAAD,
        kind="official_body",
        publisher="Kiesraad",
        title="Databank Verkiezingsuitslagen",
        url=HOST,
        license="CC0 1.0",
        license_url="https://creativecommons.org/publicdomain/zero/1.0/",
        attribution="Kiesraad; Instituut voor Nederlandse Geschiedenis",
    )
    for code in CODES:
        p.artifact(raw.cached(SOURCE, page_url(code)), KIESRAAD)
        for option in options(code):
            p.artifact(raw.cached(SOURCE, csv_url(code, stemming(code, option)["Info"]["StemmingId"])), KIESRAAD)
    p.add(
        "scheme",
        code=DISTRICTS,
        polity_id=NETHERLANDS,
        kind="electoral_district",
        rank=1,
        noun="electoral district",
        code_system="kiesraad",
    )
    p.add("scheme", code=MUNICIPAL, polity_id=NETHERLANDS, kind="precinct", rank=2, noun="municipality")
    p.add("body", id=BODY, polity_id=NETHERLANDS, kind="lower_house")
    p.name(BODY, "Tweede Kamer der Staten-Generaal", "nl", "official")
    p.name(BODY, "House of Representatives", "en", "official", False)
    names: dict[str, str] = {}
    spans: dict[str, list[date]] = {}
    municipalities: dict[tuple[str, str], str] = {}
    people: set[str] = set()
    for poll in polls():
        assert names.setdefault(poll.district, poll.name) == poll.name, (poll.district, poll.name)
        spans.setdefault(poll.district, []).append(poll.day)
        for row in poll.municipalities:
            text = " ".join(str(row["Naam"]).split())
            municipalities.setdefault((poll.district, sub_code(poll.district, text)), text)
            people |= {" ".join(str(item["Naam"]).split()) for item in row["Kandidaten"] or []}
        people |= {text for text, _, _ in poll.candidates}
    for district, text in sorted(names.items()):
        id = area(district)
        p.add("area", id=id, scheme_code=DISTRICTS)
        p.add("area_code", area_id=id, code=district)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, text, "nl", "official")
        p.add(
            "reporting_unit",
            id=unit(district),
            polity_id=NETHERLANDS,
            scheme_code=DISTRICTS,
            code=district,
            area_relation="equals",
            area_id=id,
            kind="standard",
        )
        days = spans[district]
        p.add(
            "note",
            id=key("note", id),
            entity_id=id,
            kind="caveat",
            body_md=f"The databank's district {district}, which voted from {min(days)} to {max(days)}. Its territory "
            "changed with the redistricting acts of 1850, 1888, and 1896, and the archive keeps one area for each "
            "district code the databank uses; no boundary is stored.",
            lang="en",
        )
    for (district, code), text in sorted(municipalities.items()):
        p.add(
            "reporting_unit",
            id=key("unit", MUNICIPAL, code),
            polity_id=NETHERLANDS,
            scheme_code=MUNICIPAL,
            code=code,
            parent_id=unit(district),
            area_relation="within",
            area_id=area(district),
            kind="standard",
        )
        p.name(key("unit", MUNICIPAL, code), text, "nl", "official")
    for text in sorted(people):
        p.add("person", id=person(text))
        p.name(person(text), text, "nl", "official")
    return p


def build(code: str) -> Package:
    first = entry_day(code)
    p = Package(
        package_key(code),
        SOURCE,
        f"Dutch general election of {first} by district, with its runoffs and the by-elections until the next "
        "renewal, from the Kiesraad's databank",
        depends=[f"{SOURCE}/registry"],
    )
    p.external |= {NETHERLANDS}
    p.accept(
        "accounting.ballots_sum",
        "The databank's ballot papers (stembriefjes) include invalid ballots it does not count separately, so its "
        "valid votes and blank ballots add up to fewer; a few of its municipal rows give more valid votes than "
        "ballot papers.",
    )
    mine = [poll for poll in polls() if group(poll)[0] == code]
    election_pages = {poll.entry for poll in mine}
    used: set[str] = set()
    for entry in sorted(election_pages):
        for option in options(entry):
            used.add(stemming_url(entry, option))
        election = page(entry)["Info"]["Id"]
        for region in districts(entry):
            artifact = raw.cached(SOURCE, history_url(election, region))
            if json.loads(artifact.path.read_text())["Stemregios"]:
                used.add(history_url(election, region))
    used |= {district_url(poll.region) for poll in mine}
    for url in sorted(used):
        p.artifact(raw.cached(SOURCE, url), KIESRAAD)
    groups: dict[tuple[str, str, date], list[Poll]] = {}
    for poll in mine:
        groups.setdefault(group(poll), []).append(poll)
    contests = 0
    for (_, kind, day), members in sorted(groups.items()):
        election = key("election", code, kind, day)
        base = f"nl/{day}-tweede-kamer" + ("" if kind == "general" else "-tussentijds")
        p.add(
            "election",
            id=election,
            polity_id=NETHERLANDS,
            kind=kind,
            date_from=min(poll.day for poll in members),
            date_to=max(poll.day for poll in members),
            date_precision="day",
            status="held",
        )
        if kind == "general":
            p.name(election, f"Dutch general election of {day:%-d %B %Y}", "en", "official")
            p.name(election, f"Tweede Kamerverkiezing van {dutch(day)}", "nl", "native", False)
        else:
            p.name(election, f"Dutch by-elections of {day:%-d %B %Y}", "en", "official")
            p.name(election, f"Tussentijdse Tweede Kamerverkiezingen van {dutch(day)}", "nl", "native", False)
        p.add("slug", path=base, entity_id=election, is_current=True)
        paths: set[str] = set()
        for poll in members:
            contests += 1
            contest_in(p, poll, election, base, paths)
    voters = registered = 0
    for option in options(code):
        national = stemming(code, option)["Stemregio"]
        number = int(stemming(code, option)["Info"]["StemmingId"])
        held = [poll for poll in polls() if poll.stemming == number]
        voters += whole(national["Opkomst"]) == sum(poll.figures.get(VOTERS, 0) for poll in held)
        registered += whole(national["Kiesgerechtigden"]) == sum(poll.figures.get(REGISTERED, 0) for poll in held)
    TOTALS_REPORT[str(first)] = (
        f"{len(options(code))} polling days: the districts' voters add up to the databank's national turnout on "
        f"{voters}, and their electorates to its national electorate on {registered}; "
        f"{len(groups)} elections, {contests} contests"
    )
    return p


def contest_in(p: Package, poll: Poll, election: str, base: str, paths: set[str]) -> None:
    chain = chains()[poll.region]
    contest = key("contest", poll.stemming, poll.district)
    runoff = chain.round > 1
    path = f"{base}/{changeset.slug(poll.name)}"
    if second_seat(poll):
        path += "-2e-zetel"
    if runoff:
        path += f"-herstemming-{poll.day}"
    assert path not in paths, path
    paths.add(path)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="body_seats",
        body_id=BODY,
        district_area_id=area(poll.district),
        round_number=chain.round,
        previous_round_id=None
        if chain.previous is None
        else key("contest", chain.previous.stemming, chain.previous.district),
        default_result_kind="counted",
    )
    suffix = f", herstemming van {dutch(poll.day)}" if runoff else ""
    p.name(contest, f"Kiesdistrict {poll.name}{suffix}", "nl", "official")
    english = f", runoff of {poll.day:%-d %B %Y}" if runoff else ""
    p.name(contest, f"{poll.name} district{english}", "en", "official", False)
    p.add("slug", path=path, entity_id=contest, is_current=True)
    options: dict[str, str] = {}
    for position, (text, _, recommended) in enumerate(poll.candidates, 1):
        option = options[text] = key("option", contest, text)
        p.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=text)
        p.add(
            "candidacy",
            id=key("candidacy", option),
            person_id=person(text),
            option_id=option,
            role="head",
            ballot_name=text,
        )
        if recommended:
            p.add(
                "note",
                id=key("note", option, "aanbeveling"),
                entity_id=option,
                kind="context",
                body_md=f"Recommended by (aanbeveling, as the databank gives it): {' '.join(str(recommended).split())}",
                lang="en",
            )
    result_set = key("result_set", contest, "district")
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=KIESRAAD,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
    p.add(
        "citation",
        id=key("citation", result_set),
        entity_id=result_set,
        source_id=KIESRAAD,
        locator={"url": district_url(poll.region), "stemming": poll.kind, "date": str(poll.day)},
    )
    rows: list[tuple[str, str, str | None, int, int]] = []
    for measure, value in poll.figures.items():
        rows.append((result_set, unit(poll.district), None, measure, value))
    for text, votes, _ in poll.candidates:
        rows.append((result_set, unit(poll.district), options[text], VOTES, votes))
    if poll.municipalities:
        fine = key("result_set", contest, "stemdistrict")
        total = sum(votes for _, votes, _ in poll.candidates)
        covered = 0
        seen: set[str] = set()
        for row in poll.municipalities:
            text = " ".join(str(row["Naam"]).split())
            code = sub_code(poll.district, text)
            assert code not in seen, (poll.district, poll.day, text)
            seen.add(code)
            here = sub_unit(poll.district, text)
            for field, measure in FIGURES.items():
                value = whole(row.get(field))
                if value is not None:
                    rows.append((fine, here, None, measure, value))
            for item in row["Kandidaten"] or []:
                name = " ".join(str(item["Naam"]).split())
                votes = whole(item["AantalStemmen"])
                if votes is None:
                    continue
                if name not in options:
                    options[name] = key("option", contest, name)
                    p.add("contest_option", id=options[name], contest_id=contest, kind="candidate", ballot_label=name)
                    p.add(
                        "candidacy",
                        id=key("candidacy", options[name]),
                        person_id=person(name),
                        option_id=options[name],
                        role="head",
                        ballot_name=name,
                    )
                covered += votes
                rows.append((fine, here, options[name], VOTES, votes))
        p.add(
            "result_set",
            id=fine,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=KIESRAAD,
            status="active",
            is_default=False,
            completeness=round(min(covered / total, 1.0), 6) if total else 0.0,
        )
        p.add("note", id=key("note", fine), entity_id=fine, kind="methodology", body_md=FINE_METHOD, lang="en")
    p.frame(
        "result_value",
        pl.DataFrame(
            rows,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
