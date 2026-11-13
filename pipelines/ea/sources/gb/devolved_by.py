import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import umit
from ea.store import DATA, connect

SOURCE = "gb.devolved_by"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, INVALID = 1, 2, 5
PAGE = "https://en.wikipedia.org/w/index.php?title={}&action=raw"
LICENSE = "https://creativecommons.org/licenses/by-sa/4.0/"
BY_ELECTIONS = (
    ("ayr", date(2000, 3, 16), "SCT", "Ayr", "1999:ayr", "2000_Ayr_by-election", r"(?i)by-election"),
    (
        "anniesland",
        date(2000, 11, 23),
        "SCT",
        "Glasgow Anniesland",
        "1999:glasgow-anniesland",
        "2000_Glasgow_Anniesland_by-elections",
        r"Scottish Parliament by-election",
    ),
    (
        "strathkelvin",
        date(2001, 6, 7),
        "SCT",
        "Strathkelvin and Bearsden",
        "1999:strathkelvin-and-bearsden",
        "2001_Strathkelvin_and_Bearsden_by-election",
        r"(?i)by-election",
    ),
    (
        "banff",
        date(2001, 6, 7),
        "SCT",
        "Banff and Buchan",
        "1999:banff-and-buchan",
        "2001_Banff_and_Buchan_by-election",
        r"(?i)by-election",
    ),
    (
        "cathcart",
        date(2005, 9, 29),
        "SCT",
        "Glasgow Cathcart",
        "1999:glasgow-cathcart",
        "2005_Glasgow_Cathcart_by-election",
        r"(?i)by-election",
    ),
    ("moray", date(2006, 4, 27), "SCT", "Moray", "1999:moray", "2006_Moray_by-election", r"(?i)by-election"),
    (
        "donside",
        date(2013, 6, 20),
        "SCT",
        "Aberdeen Donside",
        "2011:aberdeen-donside",
        "2013_Aberdeen_Donside_by-election",
        r"(?i)by-election",
    ),
    (
        "dunfermline",
        date(2013, 10, 24),
        "SCT",
        "Dunfermline",
        "2011:dunfermline",
        "2013_Dunfermline_by-election",
        r"Scottish Parliament by-election",
    ),
    (
        "cowdenbeath",
        date(2014, 1, 23),
        "SCT",
        "Cowdenbeath",
        "2011:cowdenbeath",
        "2014_Cowdenbeath_by-election",
        r"(?i)by-election",
    ),
    (
        "ettrick",
        date(2017, 6, 8),
        "SCT",
        "Ettrick, Roxburgh and Berwickshire",
        "2011:ettrick-roxburgh-and-berwickshire",
        "2017_Ettrick,_Roxburgh_and_Berwickshire_by-election",
        r"(?i)by-election",
    ),
    (
        "shetland",
        date(2019, 8, 29),
        "SCT",
        "Shetland Islands",
        "2011:shetland-islands",
        "2019_Shetland_by-election",
        r"(?i)by-election",
    ),
    (
        "hamilton",
        date(2025, 6, 5),
        "SCT",
        "Hamilton, Larkhall and Stonehouse",
        "2011:hamilton-larkhall-and-stonehouse",
        "2025_Hamilton,_Larkhall_and_Stonehouse_by-election",
        r"(?i)by-election",
    ),
    (
        "swansea",
        date(2001, 9, 27),
        "WLS",
        "Swansea East",
        "1999:swansea-east",
        "2001_Swansea_East_by-election",
        r"(?i)by-election",
    ),
    (
        "blaenau",
        date(2006, 6, 29),
        "WLS",
        "Blaenau Gwent",
        "1999:blaenau-gwent",
        "2006_Blaenau_Gwent_by-elections",
        r"Welsh Assembly",
    ),
    ("ynys", date(2013, 8, 1), "WLS", "Ynys Môn", "2007:ynys-mon", "2013_Ynys_Môn_by-election", r"(?i)by-election"),
    (
        "alyn",
        date(2018, 2, 6),
        "WLS",
        "Alyn and Deeside",
        "2007:alyn-and-deeside",
        "2018_Alyn_and_Deeside_by-election",
        r"(?i)by-election",
    ),
    (
        "caerphilly",
        date(2025, 10, 23),
        "WLS",
        "Caerphilly",
        "2007:caerphilly",
        "2025_Caerphilly_by-election",
        r"(?i)by-election",
    ),
)
WINNERS = {
    "john-scott": ("gb.scottish_parliament.person", "john-scott"),
    "bill-butler": ("gb.scottish_parliament.person", "bill-butler"),
    "brian-fitzpatrick": ("wikidata", "Q912167"),
    "stewart-stevenson": ("gb.scottish_parliament.person", "stewart-stevenson"),
    "charlie-gordon": ("gb.scottish_parliament.person", "charlie-gordon"),
    "richard-lochhead": ("gb.scottish_parliament.person", "richard-lochhead"),
    "mark-mcdonald": ("gb.scottish_parliament.person", "mark-mcdonald"),
    "cara-hilton": ("wikidata", "Q15101536"),
    "alex-rowley": ("gb.scottish_parliament.person", "alex-rowley"),
    "rachael-hamilton": ("gb.scottish_parliament.person", "rachael-hamilton"),
    "beatrice-wishart": ("gb.scottish_parliament.person", "beatrice-wishart"),
    "davy-russell": ("wikidata", "Q134718553"),
    "val-lloyd": ("wikidata", "Q7909111"),
    "trish-law": ("wikidata", "Q7843996"),
    "rhun-ap-iorwerth": ("wikidata", "Q14420732"),
    "jack-sargeant": ("wikidata", "Q47995095"),
    "lindsay-whittle": ("wikidata", "Q6552839"),
}
PARTIES = {
    "Scottish Conservatives": "conservative",
    "Scottish Conservative Party": "conservative",
    "Scottish Conservative and Unionist Party": "conservative",
    "Conservative Party (UK)": "conservative",
    "Welsh Conservatives": "conservative",
    "Scottish Labour": "labour",
    "Scottish Labour Party": "labour",
    "Labour Party (UK)": "labour",
    "Welsh Labour": "labour",
    "Liberal Democrats (UK)": "liberal-democrats",
    "Scottish Liberal Democrats": "liberal-democrats",
    "Welsh Liberal Democrats": "liberal-democrats",
    "Welsh Liberal Democrats (2025)": "liberal-democrats",
    "Scottish National Party": "scottish-national-party",
    "Scottish Greens": "scottish-greens",
    "Scottish Green Party": "scottish-greens",
    "Wales Green Party": "green-party",
    "Green Party of England and Wales": "green-party",
    "Plaid Cymru": "plaid-cymru",
    "UKIP": "uk-independence-party",
    "UK Independence Party": "uk-independence-party",
    "United Kingdom Independence Party": "uk-independence-party",
    "Reform UK": "reform-uk",
    "Scottish Socialist Party": "scottish-socialist-party",
    "Socialist Labour Party (UK)": "socialist-labour-party",
    "ProLife Alliance": "prolife-alliance",
    "Blaenau Gwent People's Voice Group": "blaenau-gwent-people-s-voice",
    "Official Monster Raving Loony Party": "official-monster-raving-loony-party",
    "National Front (UK)": "national-front",
    "Scottish Christian Party": "scottish-christian-party",
    "NHS First Party": "nhs-first",
    "Scottish Family Party": "scottish-family-party",
    "New Millennium Bean Party": "new-millennium-bean-party",
    "Gwlad": "gwlad",
}
NEW_PARTIES = {
    "Scottish Democratic Alliance": "scottish-democratic-alliance",
    "Save Stobhill Hospital": "save-stobhill-hospital",
    "The Radio Vet": "the-radio-vet",
    "Welsh Socialist Alliance": "welsh-socialist-alliance",
    "The Victims Final Right": "the-victims-final-right",
}
CORRECTIONS = {
    ("dunfermline", "Cara Hilton"): (
        10279,
        10275,
        (
            "Wikipedia's election box prints 10,279 votes for Cara Hilton, which makes the candidates sum to 24,204 "
            "against the 24,200 it prints as the turnout. 10,275, which an independent news report also gives, makes "
            "them sum to 24,200, so 10,275 is used."
        ),
    ),
}
NOTES = {
    "anniesland": (
        "The candidates' votes sum to 20,221 but Wikipedia's election box prints a turnout of 20,211. The percentages "
        "match a BBC News listing, so the candidate votes are kept as printed and no turnout is stored."
    ),
    "donside": (
        "The candidates' votes sum to 23,378 and Wikipedia's election box prints a turnout of 23,396. It prints no "
        "rejected-paper count, so the 18 votes' difference is left unexplained and no turnout is stored."
    ),
    "caerphilly": (
        "The candidates' votes sum to the 33,686 that Wikipedia's election box prints as the turnout, and the box "
        "prints 50 rejected papers separately, so the turnout figure counts valid votes only."
    ),
}


def fetch() -> None:
    for *_, page, _ in BY_ELECTIONS:
        raw.fetch(SOURCE, PAGE.format(quote(page, safe=",_")), suffix=".wiki")
        time.sleep(1)


def templates(text: str) -> list[str]:
    found = []
    at = 0
    while (at := text.find("{{Election box", at)) >= 0:
        depth = 0
        end = at
        while end < len(text):
            if text.startswith("{{", end):
                depth += 1
                end += 2
            elif text.startswith("}}", end):
                depth -= 1
                end += 2
                if depth == 0:
                    break
            else:
                end += 1
        found.append(text[at:end])
        at = end
    return found


def fields(block: str) -> tuple[str, dict[str, str]]:
    block = re.sub(r"<ref.*?</ref>|<ref[^>]*/>", "", block, flags=re.DOTALL)
    block = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", block)
    kind = re.match(r"\{\{Election box ([^|\n]*)", block)
    assert kind is not None
    found: dict[str, str] = {}
    for key, value in re.findall(r"\|\s*([\w. ]+?)\s*=\s*([^|\n}]*)", block):
        found.setdefault(key, value.strip())
    return kind.group(1).strip(), found


def count(text: str | None) -> int | None:
    digits = re.sub(r"[^\d]", "", text or "")
    return int(digits) if digits else None


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    lines = []
    totals = []
    for key, day, country, constituency, area, page, title in BY_ELECTIONS:
        text = Path(raw.cached(SOURCE, PAGE.format(quote(page, safe=",_"))).path).read_text()
        chosen = False
        printed = {}
        for block in templates(text):
            kind, found = fields(block)
            if kind.startswith("begin"):
                chosen = re.search(title, found.get("title", "")) is not None and not printed
                continue
            if not chosen:
                continue
            if kind.startswith(("winning", "candidate")):
                name = re.sub(r"^Dr ", "", found["candidate"])
                votes = count(found["votes"])
                label = found.get("party", "")
                fixed = CORRECTIONS.get((key, name))
                if fixed is not None:
                    assert votes == fixed[0]
                    votes = fixed[1]
                lines.append(
                    {
                        "key": key,
                        "candidate": name,
                        "party": label,
                        "votes": votes,
                        "elected": kind.startswith("winning"),
                        "correction": fixed[2] if fixed else None,
                    }
                )
            elif kind.startswith("registered"):
                printed["registered"] = count(found.get("reg. electors"))
            elif kind.startswith("rejected"):
                printed["rejected"] = count(found.get("votes"))
            elif kind.startswith("turnout"):
                printed["turnout"] = count(found.get("votes"))
        totals.append(
            {
                "key": key,
                "date": day,
                "country": country,
                "constituency": constituency,
                "area": area,
                "page": page,
                "registered": printed.get("registered"),
                "rejected": printed.get("rejected"),
                "turnout": printed.get("turnout"),
            }
        )
    candidates = pl.DataFrame(lines)
    contests = pl.DataFrame(totals)
    for row in contests.iter_rows(named=True):
        mine = candidates.filter(pl.col("key") == row["key"])
        total = mine["votes"].sum() + (row["rejected"] or 0)
        print(f"{row['key']}: {len(mine)} candidates, {mine['votes'].sum()} votes, printed turnout {row['turnout']}")
        if row["turnout"] is not None and total != row["turnout"] and row["key"] not in NOTES:
            print(f"  note: votes plus rejected {total} differ from printed turnout {row['turnout']}")
    candidates.write_parquet(PARSED / "candidates.parquet")
    contests.write_parquet(PARSED / "contests.parquet")


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    contests = pl.read_parquet(PARSED / "contests.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Scottish Parliament and Senedd constituency by-elections 2000-2025, from Wikipedia's election boxes",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        polities = {"SCT": cs.polity("GB-SCT"), "WLS": cs.polity("GB-WLS")}
        bodies = {"SCT": cs.key("gb.body", "scottish-parliament"), "WLS": cs.key("gb.body", "senedd")}
        schemes = {"SCT": "gb.spc", "WLS": "gb.senc"}
        fptp = cs.key("gb.electoral_system", "fptp")
        for body in bodies.values():
            if body in cs.minted:
                raise ValueError("publish the Scottish Parliament and Senedd general-election loads first")

        sources: dict[str, UUID] = {}
        for row in contests.iter_rows(named=True):
            publisher, title = "Wikipedia", row["page"].replace("_", " ")
            sources[row["key"]] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[row["key"]],
                kind="community",
                publisher=publisher,
                title=title,
                url=f"https://en.wikipedia.org/wiki/{quote(row['page'], safe=',_')}",
                license="CC BY-SA 4.0",
                license_url=LICENSE,
                attribution=f"Wikipedia contributors, “{title}”",
            )
            cs.artifact(raw.cached(SOURCE, PAGE.format(quote(row["page"], safe=",_"))), sources[row["key"]])

        parties: dict[str, UUID] = {}

        def party_of(label: str) -> UUID | None:
            if label.startswith("Independent"):
                return None
            key = PARTIES.get(label) or NEW_PARTIES[label]
            if key not in parties:
                if key in umit.EXISTING:
                    parties[key] = cs.key(*umit.EXISTING[key])
                else:
                    parties[key] = cs.key("gb.party", key)
                    if parties[key] in cs.minted:
                        cs.add("party", id=parties[key], kind="party", polity_id=gb)
                        name(parties[key], umit.NAMES.get(key, label))
            return parties[key]

        persons: dict[str, UUID] = {}

        def person_of(day: date, place: str, text: str) -> UUID:
            identity = slug(text) if slug(text) in WINNERS else f"{day}:{slug(place)}:{slug(text)}"
            if identity not in persons:
                persons[identity] = (
                    cs.key(*WINNERS[identity]) if identity in WINNERS else cs.key(f"{SOURCE}.person", identity)
                )
                if persons[identity] in cs.minted:
                    cs.add("person", id=persons[identity])
                    name(persons[identity], text)
            return persons[identity]

        for (day, country), group in sorted(
            ((key, frame) for key, frame in contests.group_by(["date", "country"])), key=lambda item: item[0]
        ):
            seats = group["constituency"].to_list()
            spoken = f"{day:%B} {day.day}, {day.year}"
            election = cs.key(f"{SOURCE}.election", f"{day}:{country}")
            cs.add(
                "election",
                id=election,
                polity_id=polities[country],
                kind="by_election",
                date_from=day,
                date_to=day,
                status="held",
            )
            noun = "Scottish Parliament" if country == "SCT" else "Senedd"
            name(election, f"{seats[0]} by-election, {spoken}" if len(seats) == 1 else f"{noun} by-elections, {spoken}")
            path = f"gb/{'scotland' if country == 'SCT' else 'wales'}/{day}-by-election"
            cs.add("slug", path=path, entity_id=election)
            for row in group.iter_rows(named=True):
                constituency = row["constituency"]
                code = row["area"]
                unit = cs.unit(schemes[country], code)
                contest_key = f"{day}:{country}:{slug(constituency)}"
                contest = cs.key(f"{SOURCE}.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=bodies[country],
                    district_area_id=cs.area(schemes[country], code),
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{constituency} by-election, {spoken}")
                cs.add("slug", path=f"{path}/{slug(constituency)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=sources[row["key"]],
                    is_default=True,
                    completeness=1,
                )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The figures are transcribed from the election box on Wikipedia's article, which cites "
                    "press reports and the returning officer's results; they have not been checked against the "
                    "returning officer's declaration. The candidates' votes sum to the printed turnout "
                    "(votes plus rejected papers) except where a note says otherwise.",
                )
                if row["key"] in NOTES:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=NOTES[row["key"]])

                def value(option: UUID | None, measure: int, amount: int | None, result_set: UUID = result_set) -> None:
                    if amount is None:
                        return
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=amount,
                    )

                value(None, REGISTERED, row["registered"])
                value(None, INVALID, row["rejected"])
                for line in candidates.filter(pl.col("key") == row["key"]).iter_rows(named=True):
                    option_key = f"{contest_key}:{slug(line['candidate'])}"
                    option = cs.key("gb.option", option_key)
                    party = party_of(line["party"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=line["candidate"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("gb.candidacy", option_key),
                        person_id=person_of(day, constituency, line["candidate"]),
                        option_id=option,
                        role="head",
                        ballot_name=line["candidate"],
                        member_of_party_id=party,
                        is_independent=party is None,
                    )
                    value(option, VOTES, line["votes"])
                    if line["correction"] is not None:
                        cs.add("note", entity_id=contest, kind="context", body_md=line["correction"])
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
