import json
import re
import time
import unicodedata
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import fastexcel
import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.gb import umit
from ea.store import connect

SOURCE = "gb.ep_fptp"
VERSION = "1"
ELECTIONS = {1989: date(1989, 6, 15), 1994: date(1994, 6, 9)}
TERMS = {1989: 3, 1994: 4}
TERM_START = {1989: date(1989, 7, 25), 1994: date(1994, 7, 19)}
GB_FROM = date(1979, 6, 7)
GB_TO = date(2020, 2, 1)
WIKI = "https://en.wikipedia.org/w/api.php?format=json&formatversion=2"
PAGES = {
    1989: ("List of members of the European Parliament for the United Kingdom (1989–1994)", 1366028854),
    1994: ("List of members of the European Parliament for the United Kingdom (1994–1999)", 1366028967),
}
API = "https://data.europarl.europa.eu/api/v2"
FORMAT = "format=application%2Fld%2Bjson"
NATIONAL = "def/ep-entities/NATIONAL_POLITICAL_GROUP"
SPARQL = "https://query.wikidata.org/sparql?format=json&query="
LIBRARY = (
    "https://web.archive.org/web/2020id_/https://researchbriefings.files.parliament.uk/documents/RP99-57/RP99-57.pdf"
)
LIBRARY_PAGE = "https://commonslibrary.parliament.uk/research-briefings/rp99-57/"
LIBRARY_TITLE = "European Parliament elections: 1979 to 1994 (Research Paper 99/57)"
TOTALS = {
    1989: [
        ("Labour", "labour", 6153661, 45),
        ("Conservative", "conservative", 5331098, 32),
        ("Social and Liberal Democrats", "liberal-democrats", 944861, 0),
        ("Scottish National", "scottish-national-party", 406686, 1),
        ("Plaid Cymru", "plaid-cymru", 115062, 0),
        ("Green", "green-party", 2292718, 0),
        ("SDP", "social-democratic-party-1981", 75886, 0),
        ("Other", None, 41295, 0),
    ],
    1994: [
        ("Labour", "labour", 6753881, 62),
        ("Conservative", "conservative", 4268539, 18),
        ("Liberal Democrat", "liberal-democrats", 2557887, 2),
        ("Scottish National", "scottish-national-party", 487237, 2),
        ("Plaid Cymru", "plaid-cymru", 162478, 0),
        ("Green", "green-party", 494561, 0),
        ("UK Independence", "uk-independence-party", 150251, 0),
        ("Natural Law", "natural-law-party", 96554, 0),
        ("Liberal", "liberal-party-1989", 100500, 0),
        ("Other", None, 220834, 0),
    ],
}
BRIEFING = "https://researchbriefings.files.parliament.uk/documents/CBP-7529/EP-and-devolved-legislatures.xlsx"
BRIEFING_PAGE = "https://commonslibrary.parliament.uk/research-briefings/cbp-7529/"
BRIEFING_TITLE = "UK Election Statistics: 1918-2023, A Long Century of Elections (CBP-7529)"
DELIVERED = Path(__file__).parents[4] / "data/inbox/manual/MF-069/EP-and-devolved-legislatures.xlsx"
PRINTED = {1989: 15361267, 1994: 15292722}
PARTIES = {
    "Labour Party (UK)": "labour",
    "Conservative Party (UK)": "conservative",
    "Liberal Democrats (UK)": "liberal-democrats",
    "Scottish National Party": "scottish-national-party",
}
EP_PARTIES = {"Lab.": "labour", "Cons.": "conservative", "LD": "liberal-democrats", "SNP": "scottish-national-party"}
NAMES = {
    "Cambridgeshire & Bedfordshire North": "Cambridge and Bedfordshire North",
    "Scotland South": "South of Scotland",
}
TOTALS_CAVEAT = (
    "The Commons Library's Research Paper 99/57 gives votes and seats by party for Great Britain as a whole. The "
    "members themselves are on each Euro-constituency's contest."
)
WINNER_CAVEAT = (
    "Only the elected member is loaded, from Wikipedia's list of the term's members, checked against the European "
    "Parliament's record of each member's term and national party. No official source online prints the "
    "constituency's votes; Craig and Mackie's Europe Votes is the likely print source."
)


def page(year: int) -> str:
    return f"{WIKI}&action=parse&prop=wikitext%7Crevid&oldid={PAGES[year][1]}"


def get(url: str, suffix: str = ".json") -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        for attempt in range(5):
            time.sleep(1 + 5 * attempt)
            try:
                return raw.fetch(SOURCE, url, suffix=suffix)
            except httpx.HTTPStatusError as error:
                if error.response.status_code != 429 and error.response.status_code < 500 or attempt == 4:
                    raise
        raise AssertionError


def listing(term: int, offset: int) -> str:
    return f"{API}/meps?parliamentary-term={term}&country-of-representation=GB&{FORMAT}&offset={offset}&limit=10"


def meps(term: int) -> list[dict[str, str]]:
    found = []
    for offset in range(0, 1000, 10):
        text = get(listing(term, offset)).path.read_text()
        if not text.strip():
            return found
        found += json.loads(text)["data"]
    raise ValueError(f"term {term} lists over 1,000 members")


def rows(year: int) -> list[dict[str, str]]:
    text = json.loads(get(page(year)).path.read_text())["parse"]["wikitext"]
    start = text.index("{|")
    table = text[start : text.index("|}", start)]
    found = []
    for block in table.split("\n|-")[1:]:
        cells = [cell.strip() for cell in re.split(r"\n\|\s*|\|\|", block) if cell.strip()]
        if cells[0].startswith("!"):
            continue
        if len(cells) != 4:
            raise ValueError(f"{year}: unexpected row {cells}")
        link = re.search(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", cells[0])
        if link is None:
            raise ValueError(f"{year}: no link in {cells[0]}")
        party = re.findall(r"\[\[([^|\]]+)", cells[1])
        place = re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", cells[3])
        found.append(
            {
                "title": link.group(1),
                "name": link.group(2) or link.group(1),
                "party": party[0] if party else cells[1],
                "constituency": re.sub(r"<[^>]+>.*", "", place).strip(),
            }
        )
    return found


def qids(titles: list[str]) -> dict[str, str]:
    found = {}
    for start in range(0, len(titles), 50):
        chunk = "|".join(sorted(titles)[start : start + 50])
        url = f"{WIKI}&action=query&prop=pageprops&ppprop=wikibase_item&redirects=1&titles={quote(chunk)}"
        data = json.loads(get(url).path.read_text())["query"]
        moved = {item["to"]: item["from"] for item in data.get("redirects", [])}
        moved |= {item["to"]: item["from"] for item in data.get("normalized", [])}
        for item in data["pages"]:
            title = item["title"]
            while title in moved:
                title = moved[title]
            if "pageprops" in item:
                found[title] = item["pageprops"]["wikibase_item"]
    return found


def wikidata_query(items: list[str]) -> str:
    values = " ".join(f"wd:{item}" for item in sorted(items))
    return SPARQL + quote(
        f"SELECT ?item ?ep ?mnis WHERE {{ VALUES ?item {{ {values} }} ?item wdt:P1186 ?ep . OPTIONAL {{ ?item wdt:P10428 ?mnis }} }}"
    )


def fold(text: str) -> str:
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower().replace("&", " and ")
    return " ".join(re.findall(r"[a-z]+", plain))


def fetch() -> None:
    for year, term in TERMS.items():
        listed = rows(year)
        found = qids([row["title"] for row in listed])
        get(wikidata_query(sorted(set(found.values()))))
        for mep in meps(term):
            detail = json.loads(get(f"{API}/meps/{mep['identifier']}?{FORMAT}").path.read_text())["data"][0]
            for membership in detail["hasMembership"]:
                if membership.get("membershipClassification") == NATIONAL:
                    get(f"{API}/corporate-bodies/{membership['organization'].split('/')[1]}?{FORMAT}")
    get(LIBRARY, ".pdf")
    try:
        raw.cached(SOURCE, BRIEFING)
    except LookupError:
        raw.store(
            SOURCE,
            BRIEFING,
            DELIVERED.read_bytes(),
            suffix=".xlsx",
            retrieved_at=datetime(2026, 10, 24, 15, 43, tzinfo=UTC),
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )


def words(text: str) -> frozenset[str]:
    return frozenset(fold(NAMES.get(text, text)).split()) - {"and", "the"}


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Great Britain's European Parliament elections of 1989 and 1994: votes and seats by party from the "
            "Commons Library's Research Paper 99/57, and each Euro-constituency's elected member",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        library = cs.source("House of Commons Library", LIBRARY_TITLE)
        cs.add(
            "source",
            id=library,
            kind="official_body",
            publisher="House of Commons Library",
            title=LIBRARY_TITLE,
            url=LIBRARY_PAGE,
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="Read through the Internet Archive's copy, since the Library's file host refuses scripts. Tables 1 "
            "and 3 give votes and members by party for Great Britain, 1979–1994.",
        )
        cs.artifact(raw.cached(SOURCE, LIBRARY), library)
        briefing = cs.source("House of Commons Library", BRIEFING_TITLE)
        cs.add(
            "source",
            id=briefing,
            kind="official_body",
            publisher="House of Commons Library",
            title=BRIEFING_TITLE,
            url=BRIEFING_PAGE,
            license="Open Parliament Licence v3.0",
            license_url="https://www.parliament.uk/site-information/copyright-parliament/open-parliament-licence/",
            notes="The supporting spreadsheet's sheet 1 gives European Parliament votes and seats by party for Great "
            "Britain; it counts the 1989 SDP, and the 1994 Natural Law and Liberal parties, under Other.",
        )
        sheet = raw.cached(SOURCE, BRIEFING)
        cs.artifact(sheet, briefing)
        printed = {
            int(row[0]): {int(cell) for cell in row[1:] if cell is not None and cell.isdigit()}
            for row in fastexcel.read_excel(sheet.path).load_sheet("1. EP-GB", header_row=None).to_polars().rows()
            if row[0] in ("1989", "1994")
        }
        europarl = cs.source("European Parliament", "Open Data Portal: Members of the European Parliament")
        cs.add(
            "source",
            id=europarl,
            kind="official_body",
            publisher="European Parliament",
            title="Open Data Portal: Members of the European Parliament",
            url="https://data.europarl.europa.eu/",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
        )
        gb = cs.polity("GB")
        parliament = cs.key("eu.body", "european-parliament")
        fptp = cs.key("gb.electoral_system", "fptp")
        if parliament in cs.minted or fptp in cs.minted:
            raise ValueError("publish eu.ep_cadmus first")
        great_britain = cs.area("gb.nation", "GB")
        if great_britain in cs.minted:
            cs.add("area", id=great_britain, scheme_code="gb.nation")
            name(great_britain, "Great Britain")
            cs.add("area_code", area_id=great_britain, code="GB", valid_from=GB_FROM, valid_to=GB_TO)
            cs.add(
                "area_parent",
                area_id=great_britain,
                parent_id=cs.area("gb.nation", "GBR"),
                valid_from=GB_FROM,
                valid_to=GB_TO,
            )
        whole = cs.unit("gb.nation", "GB")
        if whole in cs.minted:
            cs.add(
                "reporting_unit",
                id=whole,
                polity_id=gb,
                scheme_code="gb.nation",
                code="GB",
                area_relation="equals",
                area_id=great_britain,
                kind="standard",
                valid_from=GB_FROM,
                valid_to=GB_TO,
            )
            name(whole, "Great Britain")
        parties: dict[str, UUID] = {}

        def party(key: str) -> UUID:
            if key not in parties:
                parties[key] = cs.key(*umit.EXISTING[key]) if key in umit.EXISTING else cs.key("gb.party", key)
                if parties[key] in cs.minted:
                    raise ValueError(f"no gb.party {key}")
            return parties[key]

        known = {
            (scheme, value): entity
            for scheme, value, entity in conn.execute(
                """select scheme, value, entity_id from external_id x where scheme in ('wikidata', 'mnis', 'ep.mep')
                     and not exists (select from entity_merge m where m.entity_id = x.entity_id)"""
            ).fetchall()
        }
        for year, term in TERMS.items():
            day = ELECTIONS[year]
            election = cs.key("eu.election", f"GB:{year}:european-parliament")
            if election in cs.minted:
                raise ValueError(f"no {year} European Parliament election in the United Kingdom")
            path = f"gb/{day}-european-parliament"
            contest_key = f"GB:{year}:gb.nation:GB"
            contest = cs.key("eu.contest", contest_key)
            seats = sum(line[3] for line in TOTALS[year])
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=parliament,
                district_area_id=great_britain,
                magnitude=seats,
                electoral_system_id=fptp,
                default_result_kind="counted",
            )
            name(contest, f"{year} European Parliament election in Great Britain")
            cs.add("slug", path=f"{path}/great-britain", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=library,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=TOTALS_CAVEAT)
            cs.add("citation", entity_id=result_set, source_id=briefing)
            total = sum(line[2] for line in TOTALS[year])
            if total not in printed[year] or any(
                votes not in printed[year] for _, key, votes, _ in TOTALS[year] if key is not None and votes > 150000
            ):
                raise ValueError(f"{year}: CBP-7529 prints {printed[year]}")
            if total != PRINTED[year]:
                raise ValueError(f"{year}: parties sum to {total}, the paper prints {PRINTED[year]}")
            for position, (label, key, votes, won) in enumerate(TOTALS[year], start=1):
                option = cs.key(f"{SOURCE}.option", f"{contest_key}:{slug(label)}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="others" if key is None else "list",
                    party_id=None if key is None else party(key),
                    ballot_position=position,
                    ballot_label=label,
                )
                for measure, value in ((1, votes), (16, won)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=whole,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=whole,
                option_id=None,
                channel=0,
                measure=4,
                count_round=0,
                flags=0,
                value=total,
            )

            title, revision = PAGES[year]
            wikipedia = cs.source("Wikipedia", title)
            cs.add(
                "source",
                id=wikipedia,
                kind="community",
                publisher="Wikipedia",
                title=title,
                url=f"https://en.wikipedia.org/w/index.php?oldid={revision}",
                license="CC BY-SA 4.0",
                license_url="https://creativecommons.org/licenses/by-sa/4.0/",
            )
            cs.artifact(get(page(year)), wikipedia)
            listed = [row for row in rows(year) if fold(row["constituency"]) != "northern ireland"]
            found = qids([row["title"] for row in rows(year)])
            bindings = json.loads(get(wikidata_query(sorted(set(found.values())))).path.read_text())["results"]
            items = {
                b["item"]["value"].rsplit("/", 1)[1]: (b["ep"]["value"], b.get("mnis", {}).get("value"))
                for b in bindings["bindings"]
            }
            members = {mep["identifier"] for mep in meps(term)}
            areas = {
                words(text): (code, area)
                for code, area, text in conn.execute(
                    """select c.code, a.id, n.text from area a join area_code c on c.area_id = a.id
                       join name n on n.entity_id = a.id and n.is_preferred and n.lang = 'en'
                       where a.scheme_code = 'gb.euc' and c.valid @> %s::date""",
                    [day],
                ).fetchall()
            }
            won: dict[str, int] = {}
            for row in listed:
                qid = found[row["title"]]
                ep, mnis = items[qid]
                if ep not in members:
                    raise ValueError(f"{year}: {row['name']} ({ep}) is not a member of term {term}")
                artifact = get(f"{API}/meps/{ep}?{FORMAT}")
                cs.artifact(artifact, europarl)
                detail = json.loads(artifact.path.read_text())["data"][0]
                mandates = [
                    held["memberDuring"]["startDate"]
                    for held in detail["hasMembership"]
                    if held.get("organization") == f"org/ep-{term}"
                    and held.get("role") == "def/ep-roles/MEMBER_PARLIAMENT"
                ]
                if not mandates or min(mandates) != str(TERM_START[year]):
                    raise ValueError(f"{year}: {row['name']} took the seat on {mandates}, not at the term's start")
                national = [
                    json.loads(
                        get(f"{API}/corporate-bodies/{held['organization'].split('/')[1]}?{FORMAT}").path.read_text()
                    )["data"][0]["label"]
                    for held in detail["hasMembership"]
                    if held.get("membershipClassification") == NATIONAL
                    and held["memberDuring"]["startDate"]
                    <= str(TERM_START[year])
                    <= held["memberDuring"].get("endDate", "9999")
                ]
                key = PARTIES[row["party"]]
                if [EP_PARTIES.get(label) for label in national] != [key]:
                    raise ValueError(f"{year}: {row['name']} is {row['party']} on Wikipedia, {national} at the EP")
                won[key] = won.get(key, 0) + 1
                code, area = areas.pop(words(row["constituency"]))
                held_key = f"GB:{year}:gb.euc:{code}"
                contest = cs.key("eu.contest", held_key)
                text = cs.conn.execute(
                    "select text from name where entity_id = %s and is_preferred and lang = 'en'", [area]
                ).fetchone()[0]
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=parliament,
                    district_area_id=area,
                    magnitude=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} European Parliament election in United Kingdom, {text}")
                cs.add("slug", path=f"{path}/{slug(text)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", held_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=wikipedia,
                    is_default=True,
                    completeness=0,
                )
                cs.add("note", entity_id=result_set, kind="caveat", body_md=WINNER_CAVEAT)
                held = [("wikidata", qid), ("ep.mep", ep)] + ([("mnis", mnis)] if mnis else [])
                person = next((known[id] for id in held if id in known), None)
                if person is None:
                    person = cs.key(*held[0])
                    cs.add("person", id=person)
                    name(person, row["name"])
                    known[held[0]] = person
                for id in held:
                    if id not in known:
                        known[id] = person
                        cs.add("external_id", entity_id=person, scheme=id[0], value=id[1])
                option = cs.key(f"{SOURCE}.option", f"{held_key}:{ep}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party(key),
                    ballot_label=row["name"],
                )
                candidacy = cs.key(f"{SOURCE}.candidacy", f"{held_key}:{ep}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["name"],
                    member_of_party_id=party(key),
                )
                cs.add("citation", entity_id=candidacy, source_id=europarl)
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            if areas:
                raise ValueError(f"{year}: no member for {sorted(code for code, _ in areas.values())}")
            declared = {key: seated for _, key, _, seated in TOTALS[year] if key is not None and seated}
            if won != declared:
                raise ValueError(f"{year}: members by party {won} against the paper's {declared}")
            print(year, len(listed), "members", won)
        return cs.write()
