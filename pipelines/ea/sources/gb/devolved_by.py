import re
import time
from datetime import date
from html import unescape
from pathlib import Path
from typing import NotRequired, TypedDict
from urllib.parse import quote
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import umit
from ea.store import DATA, connect

SOURCE = "gb.devolved_by"
VERSION = "5"
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
WAYBACK = "https://web.archive.org/web/{}id_/{}"


class Official(TypedDict):
    publisher: str
    title: str
    url: str
    archive: str | None
    registered: int | None
    rejected: int | None
    votes: tuple[int, ...]
    unlisted: NotRequired[tuple[int, ...]]
    text: str
    note: str


OFFICIAL: dict[str, Official] = {
    "hamilton": {
        "publisher": "South Lanarkshire Council",
        "title": "Declaration of Result, Hamilton, Larkhall and Stonehouse Constituency By-election, 5 June 2025",
        "url": "https://www.southlanarkshire.gov.uk/info/200237/elections/2282/scottish_parliamentary_by-election_-_hamilton_larkhall_and_stonehouse_-_thursday_5_june_2025/2",
        "archive": WAYBACK.format(
            "20251119054621",
            "https://www.southlanarkshire.gov.uk/info/200237/elections/2282/scottish_parliamentary_by-election_-_hamilton_larkhall_and_stonehouse_-_thursday_5_june_2025/2",
        ),
        "registered": 61485,
        "rejected": 46,
        "votes": (8559, 7957, 7088, 1621, 695, 533, 278, 219, 109, 50),
        "text": "Electorate 61,485 Total votes cast (per verification sheet) 27,155",
        "note": (
            "The returning officer's declaration prints an electorate of 61,485, 27,155 total votes including 46 "
            "rejected ballot papers (7 for more than one candidate, 39 unmarked or void for uncertainty), and a "
            "42.2 percent poll."
        ),
    },
    "caerphilly": {
        "publisher": "Caerphilly County Borough Council",
        "title": "Caerphilly Senedd By-Election - 23 October 2025, declaration",
        "url": "https://www.caerphilly.gov.uk/my-council/voting-and-elections/election-results/caerphilly-senedd-by-election-23-october-2025",
        "archive": WAYBACK.format(
            "20251024185609",
            "https://www.caerphilly.gov.uk/my-council/voting-and-elections/election-results/caerphilly-senedd-by-election-23-october-2025",
        ),
        "registered": None,
        "rejected": 50,
        "votes": (15961, 12113, 3713, 690, 516, 497, 117, 79),
        "text": "The total number of ballot papers rejected was 50",
        "note": (
            "The returning officer's declaration prints 33,686 as the votes cast, which is the sum of the "
            "candidates' votes, and 50 rejected ballot papers (20 for more than one candidate, 30 unmarked or void "
            "for uncertainty). It prints no electorate; the 66,895 is from Wikipedia."
        ),
    },
    "shetland": {
        "publisher": "Shetland Islands Council",
        "title": "Scottish Parliamentary By-Election 2019, Shetland Islands Constituency, 29 August 2019",
        "url": "https://www.shetland.gov.uk/election-results/scottish-parliamentary-election-2019",
        "archive": WAYBACK.format(
            "20250413122014", "https://www.shetland.gov.uk/election-results/scottish-parliamentary-election-2019"
        ),
        "registered": None,
        "rejected": 11,
        "votes": (5659, 3822, 1286, 425, 189, 152, 134, 66, 60, 31),
        "text": "The total number of ballot papers rejected was 11",
        "note": (
            "The council's results page prints 11,835 votes cast, which is the candidates' 11,824 votes plus the 11 "
            "rejected ballot papers it prints, and a 66.5 percent turnout. It prints no electorate."
        ),
    },
    "ettrick": {
        "publisher": "Scottish Borders Council",
        "title": "Scottish Parliament by-election 2017, Ettrick, Roxburghshire, Berwickshire",
        "url": "https://www.scotborders.gov.uk/info/20071/election_results/610/scottish_parliament_election_results/3",
        "archive": None,
        "registered": 55395,
        "rejected": None,
        "votes": (20658, 11320, 3406, 3196),
        "text": "Total Electorate: 55,395",
        "note": (
            "The council's results page prints an electorate of 55,395 and 38,653 total votes cast, 73 more than "
            "the candidates' 38,580 votes. It prints no rejected-paper count, so none is stored."
        ),
    },
    "ynys": {
        "publisher": "Senedd Cymru",
        "title": "Election results for Ynys Môn, 1 August 2013",
        "url": "https://business.senedd.wales/mgElectionAreaResults.aspx?ID=50",
        "archive": WAYBACK.format("2021", "https://business.senedd.wales/mgElectionAreaResults.aspx?ID=50"),
        "registered": None,
        "rejected": 44,
        "votes": (12601, 3435, 3099, 1843, 348),
        "unlisted": (309,),
        "text": "Valid votes cast 21326 Number of ballot papers rejected 44",
        "note": (
            "The Senedd's published results print five candidates, 21,326 valid votes (their sum), and 44 rejected "
            "ballot papers, and omit Stephen Churchman, the sixth nominated candidate. His 309 votes are Wikipedia's "
            "and make the 21,635 turnout and the 58.2 percent for Plaid Cymru that Senedd Research prints. The "
            "Senedd page prints no electorate."
        ),
    },
}
NOTES = {
    "anniesland": (
        "The candidates' votes sum to 20,221 but Wikipedia's election box prints a turnout of 20,211. The percentages "
        "match a BBC News listing, so the candidate votes are kept as printed and no turnout is stored. The "
        "returning officer's declaration could not be reached to settle the 10 votes' difference."
    ),
    "donside": (
        "The candidates' votes sum to 23,378 and Wikipedia's election box prints a turnout of 23,396. Neither "
        "Wikipedia nor any source found prints a rejected-paper count, and Aberdeen City Council's declaration could "
        "not be reached, so the 18 votes' difference is left unexplained and no turnout is stored."
    ),
    "dunfermline": (
        "Fife Council's declaration could not be reached. Cara Hilton's 10,275 votes are used because they agree "
        "with the 2,873 majority over the SNP's 7,402 that The Scotsman reported and with Lib Dem Voice's count-night "
        "listing, while the election box's 10,279 does not."
    ),
}


def fetch() -> None:
    for *_, page, _ in BY_ELECTIONS:
        raw.fetch(SOURCE, PAGE.format(quote(page, safe=",_")), suffix=".wiki")
        time.sleep(1)
    for official in OFFICIAL.values():
        raw.fetch(SOURCE, official["archive"] or official["url"], suffix=".html")
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


def official_text(key: str) -> str:
    official = OFFICIAL[key]
    html = Path(raw.cached(SOURCE, official["archive"] or official["url"]).path).read_text()
    html = re.sub(r"<script.*?</script>|<style.*?</style>", "", html, flags=re.DOTALL)
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


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
        printed: dict[str, int | None] = {}
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
        if key in OFFICIAL:
            official = OFFICIAL[key]
            page_text = official_text(key)
            mine = sorted(int(line["votes"] or 0) for line in lines if line["key"] == key)
            assert official["text"] in page_text, key
            assert mine == sorted(official["votes"] + official.get("unlisted", ())), key
            assert all(f"{votes:,}" in page_text or f" {votes} " in page_text for votes in official["votes"]), key
            if official["registered"] is not None:
                assert printed.get("registered") in (None, official["registered"]), key
                printed["registered"] = official["registered"]
            if official["rejected"] is not None:
                assert printed.get("rejected") in (None, official["rejected"]), key
                printed["rejected"] = official["rejected"]
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
            summary="Scottish Parliament and Senedd constituency by-elections 2000-2025, from Wikipedia's election boxes and returning officers' declarations where reachable",
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

        declared: dict[str, UUID] = {}
        for key, official in OFFICIAL.items():
            declared[key] = cs.source(official["publisher"], official["title"])
            cs.add(
                "source",
                id=declared[key],
                kind="official_body",
                publisher=official["publisher"],
                title=official["title"],
                url=official["url"],
            )
            cs.artifact(raw.cached(SOURCE, official["archive"] or official["url"]), declared[key])

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
                    source_id=declared.get(row["key"], sources[row["key"]]),
                    is_default=True,
                    completeness=1,
                )
                official = OFFICIAL.get(row["key"])
                if official is None:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="The figures are transcribed from the election box on Wikipedia's article, which "
                        "cites press reports and the returning officer's results; they have not been checked against "
                        "the returning officer's declaration, which could not be reached. The candidates' votes sum "
                        "to the printed turnout (votes plus rejected papers) except where a note says otherwise.",
                    )
                else:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="The candidates' votes were checked against the returning officer's declaration "
                        f"published by {official['publisher']} and agree with Wikipedia's election box. "
                        + official["note"],
                    )
                if row["key"] in NOTES:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=NOTES[row["key"]])

                def value(
                    option: UUID | None,
                    measure: int,
                    amount: int | None,
                    result_set: UUID = result_set,
                    stated: bool = False,
                    row: dict = row,
                ) -> None:
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
                    cs.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        source_id=declared[row["key"]] if stated else sources[row["key"]],
                    )

                value(
                    None,
                    REGISTERED,
                    row["registered"],
                    stated=official is not None and official["registered"] is not None,
                )
                value(None, INVALID, row["rejected"], stated=official is not None and official["rejected"] is not None)
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
                    value(option, VOTES, line["votes"], stated=official is not None)
                    if line["correction"] is not None:
                        cs.add("note", entity_id=contest, kind="context", body_md=line["correction"])
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        noted = {(row["entity_id"], row["body_md"]) for row in cs.rows["note"]}
        entities = [row["id"] for table in ("result_set", "contest") for row in cs.rows[table]]
        for note, entity, text in conn.execute(
            """select n.id, n.entity_id, n.body_md from note n join changeset c on c.id = n.changeset_id
               where n.entity_id = any(%s) and c.pipeline = %s""",
            [entities, SOURCE],
        ).fetchall():
            if (entity, text) not in noted:
                cs.add(
                    "note_drop",
                    note_id=note,
                    entity_id=entity,
                    body_md=text,
                    reason="The note is reworded or no longer applies after the check against declarations.",
                )
        return cs.write()
