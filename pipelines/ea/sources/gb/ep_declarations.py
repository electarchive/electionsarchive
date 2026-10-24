import csv
import html
import json
import re
import subprocess
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "gb.ep_declarations"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
LIBRARY = "House of Commons Library"
PAPERS = {
    1999: (
        "https://web.archive.org/web/2020id_/https://researchbriefings.files.parliament.uk/documents/RP99-64/RP99-64.pdf",
        "Research Paper 99/64: Elections to the European Parliament, June 1999",
    ),
    2009: (
        "https://web.archive.org/web/20160203094923id_/http://researchbriefings.files.parliament.uk/documents/RP09-53/RP09-53.pdf",
        "Research Paper 09/53: European Parliament Elections 2009",
    ),
    2014: (
        "https://web.archive.org/web/2020id_/https://researchbriefings.files.parliament.uk/documents/RP14-32/RP14-32.pdf",
        "Research Paper 14/32: European Parliament Elections 2014",
    ),
    2019: (
        "https://web.archive.org/web/2021id_/https://researchbriefings.files.parliament.uk/documents/CBP-8600/CBP-8600.pdf",
        "Briefing Paper CBP-8600: European Parliament Elections 2019",
    ),
}
EUROPARL = (
    "https://web.archive.org/web/20090708161921id_/http://www.europarl.org.uk/section/2004/2004-election-results",
    "UK Office of the European Parliament",
    "2004 Election Results (from the Regional Returning Officers and the Department for Constitutional Affairs)",
)
LONDON = (
    "https://haringey.gov.uk/sites/default/files/2023-10/epe_2019_overall_london_votes.pdf",
    "Regional Returning Officer, London Region",
    "Declaration of result, European Parliamentary Election, 23 May 2019, London Region",
)
API = "https://data.europarl.europa.eu/api/v2"
FORMAT = "format=application%2Fld%2Bjson"
TERMS = {1999: 5, 2004: 6, 2009: 7, 2014: 8, 2019: 9}
NATIONAL = "def/ep-entities/NATIONAL_POLITICAL_GROUP"
SPARQL = "https://query.wikidata.org/sparql?format=json&query="
REGIONS = {
    "east-midlands": "East Midlands",
    "eastern": "Eastern",
    "london": "London",
    "north-east": "North East",
    "north-west": "North West",
    "south-east": "South East",
    "south-west": "South West",
    "west-midlands": "West Midlands",
    "yorkshire-and-the-humber": "Yorkshire and the Humber",
    "wales": "Wales",
    "scotland": "Scotland",
}
REGION_LABELS = {"east": "eastern", "yorkshire-the-humber": "yorkshire-and-the-humber", "yorkshire-and-humber": "yorkshire-and-the-humber"}
PCON_NAMES = {
    "Regent's Park & Kensington North": "Regent's Park and North Kensington",
    "Durham, City of": "City of Durham",
    "Chester, City of": "City of Chester",
    "Richmond": "Richmond (Yorks)",
    "York, City of": "City of York",
}
PARTIES = {
    "Con": "conservative",
    "Conservative": "conservative",
    "Conservative Party": "conservative",
    "Conservatives": "conservative",
    "Lab": "labour",
    "Labour": "labour",
    "Labour Party": "labour",
    "LDem": "liberal-democrats",
    "Liberal Democrat": "liberal-democrats",
    "Liberal Democrats": "liberal-democrats",
    "Green": "green-party",
    "Green Party": "green-party",
    "UKIP": "uk-independence-party",
    "UK Independence Party": "uk-independence-party",
    "SNP": "scottish-national-party",
    "Scottish National Party": "scottish-national-party",
    "PC": "plaid-cymru",
    "Plaid Cymru": "plaid-cymru",
    "Plaid Cymru - The Party of Wales": "plaid-cymru",
    "BNP": "british-national-party",
    "British National Party": "british-national-party",
    "Brexit": "reform-uk",
    "The Brexit Party": "reform-uk",
    "Brexit Party": "reform-uk",
    "Change UK": "the-independent-group-for-change",
    "English Democrats": "english-democrats",
    "English Democrats Party": "english-democrats",
    "Respect -Unity Coalition": "respect-party",
    "Respect - Unity Coalition": "respect-party",
    "Christian Peoples Alliance": "christian-peoples-alliance",
    "Christian Peoples Alliance Party": "christian-peoples-alliance",
    "Socialist Labour Party": "socialist-labour-party",
    "Liberal Party": "liberal-party-1989",
    "Liberal": "liberal-party-1989",
    "Scottish Socialist Party": "scottish-socialist-party",
    "Senior Citizens Party": "senior-citizens-party",
    "Mebyon Kernow": "mebyon-kernow",
    "Forward Wales": "forward-wales",
    "Animal Welfare Party": "animal-welfare-party",
    "UKEUP": "uk-european-union-party",
    "Women's Equality Party": "women-s-equality-party",
    "Alliance for Green Socialism": "alliance-for-green-socialism",
    "Christian Democratic Party": "christian-democratic-party",
    "Countryside": "countryside-party",
    "The Countryside Party": "countryside-party",
    "Operation Christian Vote": "christian-vote",
    "Peace Party": "peace-party",
    "Pensioners Party": "pensioners-party",
    "Prolife": "prolife-alliance",
    "Respect - the Unity Coalition": "respect-party",
    "Scottish Conservative and Unionist Party": "conservative",
    "Scottish Green Party": "scottish-greens",
    "Scottish Labour Party": "labour",
    "Scottish Wind Watch": "scottish-wind-watch",
    "Senior Citizens": "senior-citizens-party",
    "The Common Good": "common-good",
    "The Peoples Party for Better Government": "the-peoples-party-for-better-government",
}
NEW_PARTIES = {
    "scottish-wind-watch": "Scottish Wind Watch",
    "the-peoples-party-for-better-government": "The Peoples Party for Better Government",
}
EP_IDS = {
    (1999, "east-midlands", "William Newton-Dunn"): ["1394"],
    (1999, "eastern", "Jeffrey Tilford"): ["4515"],
    (1999, "london", "Theresa Villers"): ["4520"],
    (1999, "north-east", "Mo O'Toole"): ["4534"],
    (1999, "wales", "Jillian Evans"): ["4550"],
    (1999, "scotland", "Catherine Taylor"): ["4545"],
    (2004, "east-midlands", "Chris Heaton-Harris"): ["4517"],
    (2009, "south-west", "William, Earl of Dartmouth"): ["96958"],
    (2019, "north-west", "Henrik Nielsen"): ["197712"],
    (2019, "east-midlands", "Annunziata Rees-Mogg"): ["197612"],
    (2019, "east-midlands", "William Dunn"): ["1394"],
    (2019, "west-midlands", "Andrew Kerr"): ["197803"],
    (2019, "south-east", "Alexandra Phillips"): ["99461", "197710"],
    (2019, "south-east", "Belinda De Camborne Lucy"): ["197736"],
}
EXISTING = {"conservative": ("wikidata", "Q9626"), "labour": ("wikidata", "Q9630")}
SNP_PC = {"wales": "plaid-cymru", "scotland": "scottish-national-party"}
NATIONAL_PARTIES = {
    "Conservative Party": "conservative",
    "Labour Party": "labour",
    "Liberal Democrats Party": "liberal-democrats",
    "Liberal Democrats": "liberal-democrats",
    "United Kingdom Independence Party": "uk-independence-party",
    "UK Independence Party": "uk-independence-party",
    "Green Party": "green-party",
    "Scottish National Party": "scottish-national-party",
    "Plaid Cymru - Party of Wales": "plaid-cymru",
    "Plaid Cymru": "plaid-cymru",
    "The Brexit Party": "reform-uk",
    "Brexit Party": "reform-uk",
    "BREX": "reform-uk",
    "Cons.": "conservative",
    "GP": "green-party",
    "LD": "liberal-democrats",
    "Lab.": "labour",
    "SNP": "scottish-national-party",
    "UKIP": "uk-independence-party",
}
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
LINE = re.compile(r"^\s*(\S.*?\S)\s{3,}([\d,]+)\s*$")


def number(text: str) -> int:
    return int(text.replace(",", ""))


def folded(text: str) -> str:
    return re.sub(r"[^a-z]+", " ", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()).strip()


def pdf(url: str) -> str:
    return subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
    ).stdout


def get(url: str, suffix: str = ".json") -> raw.Artifact:
    try:
        return raw.cached(SOURCE, url)
    except LookupError:
        for attempt in range(5):
            time.sleep(1 + 5 * attempt)
            try:
                return raw.fetch(SOURCE, url, suffix=suffix)
            except httpx.HTTPStatusError as error:
                if error.response.status_code < 500 or attempt == 4:
                    raise
        raise AssertionError


def listing(term: int, offset: int) -> str:
    return f"{API}/meps?parliamentary-term={term}&country-of-representation=GB&{FORMAT}&offset={offset}&limit=10"


def meps(term: int, fetching: bool = False) -> list[dict[str, str]]:
    found = []
    for offset in range(0, 1000, 10):
        text = (get if fetching else lambda url: raw.cached(SOURCE, url))(listing(term, offset)).path.read_text()
        if not text.strip():
            return found
        found += json.loads(text)["data"]
    raise ValueError(f"term {term} lists over 1,000 members")


def wikidata_query() -> str:
    ids = sorted({mep["identifier"] for term in TERMS.values() for mep in meps(term)}, key=int)
    values = " ".join(f'"{id}"' for id in ids)
    return SPARQL + quote(
        f"SELECT ?item ?ep ?mnis ?label WHERE {{ VALUES ?ep {{ {values} }} ?item wdt:P1186 ?ep . "
        f"OPTIONAL {{ ?item wdt:P10428 ?mnis }} OPTIONAL {{ ?item rdfs:label ?label FILTER(lang(?label) = 'en') }} }}"
    )


def fetch() -> None:
    for url, _ in PAPERS.values():
        get(url, ".pdf")
    get(EUROPARL[0], ".html")
    get(LONDON[0], ".pdf")
    for year, term in TERMS.items():
        listed = meps(term, fetching=True)
        if year < 2014:
            continue
        for mep in listed:
            detail = json.loads(get(f"{API}/meps/{mep['identifier']}?{FORMAT}").path.read_text())["data"][0]
            for membership in detail["hasMembership"]:
                if membership.get("membershipClassification") == NATIONAL:
                    get(f"{API}/corporate-bodies/{membership['organization'].split('/')[1]}?{FORMAT}")
    get(wikidata_query())


def region_of(label: str) -> str:
    key = slug(label.replace("&", "and"))
    return REGION_LABELS.get(key, key)


def parse() -> None:
    lines: list[dict[str, object]] = []
    members: list[dict[str, object]] = []

    def line(year: int, region: str, label: str, kind: str, value: int, unit: str | None = None, seats: int | None = None) -> None:
        lines.append({"year": year, "region": region, "unit": unit, "label": label, "kind": kind, "value": value, "seats": seats})

    text = pdf(PAPERS[1999][0])
    start = text.index("Votes cast by parliamentary constituency")
    table = text[start : text.index("Table 12", start)]
    region = None
    columns = {7: ("Con", "Lab", "LDem", "Green", "UKIP", "Others"), 8: ("Con", "Lab", "LDem", "SNP/PC", "Green", "UKIP", "Others")}
    for row in table.splitlines():
        found = re.match(r"^(.*?\S)\s{2,}([\d,\s]+)$", row)
        if found and re.search(r"\d", found[2]) and region is not None:
            figures = [number(figure) for figure in found[2].split()]
            *votes, total = figures
            if sum(votes) != total:
                raise ValueError(f"1999 {found[1]}: parties sum to {sum(votes)}, not {total}")
            name = found[1].rstrip("*")
            for label, value in zip(columns[len(figures)], votes, strict=True):
                line(1999, region, label, "option", value, PCON_NAMES.get(name, name))
            line(1999, region, "valid", "valid", total, PCON_NAMES.get(name, name))
        elif row.strip() and not re.search(r"\d|Table|Votes cast|Con\s+Lab|RESEARCH", row) and len(row.strip()) < 40:
            region = region_of(row.strip())
    with open(Path(__file__).with_name("ep_declarations_1999.tsv"), newline="") as handle:
        for row in csv.DictReader(handle, delimiter="\t"):
            members.append({"year": 1999, "region": row["region"], "round": None, "name": row["name"], "party": row["party"]})

    page = raw.cached(SOURCE, EUROPARL[0]).path.read_text(encoding="latin-1")
    order = iter(["eastern", "east-midlands", "london", "north-east", "north-west", "south-east", "south-west",
                  "west-midlands", "yorkshire-and-the-humber", "scotland", "wales", None])
    region = None
    for row in re.findall(r"(?s)<tr[^>]*>(.*?)</tr>", page):
        cells = [
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()
            for cell in re.findall(r"(?s)<t[dh][^>]*>(.*?)</t[dh]>", row)
        ]
        if cells[:1] == ["Position"]:
            region = next(order)
        elif region is None:
            continue
        elif len(cells) == 3 and re.fullmatch(r"\d+\.", cells[0]):
            members.append({"year": 2004, "region": region, "round": int(cells[0][:-1]), "name": cells[1], "party": cells[2]})
        elif len(cells) == 8 and cells[0].startswith("Total valid votes"):
            line(2004, region, "valid", "valid", number(cells[1]))
        elif len(cells) == 8 and cells[0] == "Spoiled / rejected":
            line(2004, region, "rejected", "rejected", number(cells[1]))
        elif len(cells) == 8 and cells[0] == "Total of votes 2004":
            line(2004, region, "ballots", "ballots", number(cells[1]))
            line(1999, region, "valid", "printed_valid", number(cells[5]))
        elif len(cells) == 8 and cells[0] == "Electorate 2004":
            line(2004, region, "electorate", "electorate", number(cells[1]))
            line(1999, region, "electorate", "electorate", number(cells[5]))
        elif len(cells) == 8 and cells[0] != "-" and re.fullmatch(r"[\d,]+", cells[1]):
            line(2004, region, cells[0], "option", number(cells[1]), seats=0 if cells[3] == "-" else int(cells[3]))

    for year in (2009, 2014, 2019):
        rows = pdf(PAPERS[year][0]).splitlines()
        for at, row in enumerate(rows):
            if found := re.match(r"\s+MEPs elected by round: (.+)$", row):
                region = region_of(found[1].strip())
                if region not in REGIONS:
                    continue
                for following in rows[at + 1 : at + 15]:
                    if elected := re.match(r"^\s*(\d+)\s{2,}(\S.*?)(?:\s{2,}(\S.*?))?\s*$", following):
                        members.append(
                            {"year": year, "region": region, "round": int(elected[1]), "name": elected[2], "party": elected[3]}
                        )
            elif found := re.match(r"\s+Summary: (.+)$", row):
                region = region_of(found[1].strip())
                if region not in REGIONS or (year, region) == (2019, "london"):
                    continue
                for following in rows[at + 1 : at + 25]:
                    if total := re.match(r"^\s*Total\s+([\d,]+)", following):
                        line(year, region, "valid", "valid", number(total[1]))
                        break
                    if party := re.match(r"^\s+(\S.*?\S)\s{2,}([\d,]{3,})\s+[\d.]+%(?:\s+[-+][\d.]+%)?\s+(\d+)(?:\s+(?:[-+]\d+|-))?\s*$", following):
                        line(year, region, party[1], "option", number(party[2]), seats=int(party[3]))
                electorate = next(re.search(r"Electorate:\s+([\d,]+)", r) for r in rows[at:] if "Electorate:" in r)
                line(year, region, "electorate", "electorate", number(electorate[1]))

    declaration = pdf(LONDON[0])
    votes, rejected = declaration.split("rejected ballot papers", 1)
    for row in votes.splitlines():
        if (found := LINE.match(row)) and "Number of Votes" not in row:
            line(2019, "london", found[1], "option", number(found[2]))
    for row in rejected.splitlines():
        if (found := LINE.match(row)) and found[1] == "TOTAL":
            line(2019, "london", "rejected", "rejected", number(found[2]))
    totals = re.search(r"([\d,]{7,})\s+([\d,]{7,})\s+[\d.]+%", rejected)
    if totals is None:
        raise ValueError("London 2019: no electorate and ballot paper totals")
    line(2019, "london", "electorate", "electorate", number(totals[1]))
    line(2019, "london", "ballots", "ballots", number(totals[2]))

    frame = pl.DataFrame(lines, infer_schema_length=None)
    elected = pl.DataFrame(members, infer_schema_length=None)
    wikidata = {
        row["ep"]["value"]: (
            row["item"]["value"].rsplit("/", 1)[1],
            row.get("mnis", {}).get("value"),
            row.get("label", {}).get("value"),
        )
        for row in json.loads(raw.cached(SOURCE, wikidata_query()).path.read_text())["results"]["bindings"]
    }
    matched = []
    taken: dict[tuple[int, str, str], int] = {}
    for row in elected.iter_rows(named=True):
        candidates = meps(TERMS[row["year"]])
        name = folded(row["name"])
        hits = [m for m in candidates if folded(f"{m.get('givenName', '')} {m.get('familyName', '')}") == name] or [
            m for m in candidates if folded(m.get("familyName", "")) == name.split()[-1] and folded(m.get("givenName", ""))[:1] == name[:1]
        ] or [m for m in candidates if folded(m.get("familyName", "")) == name.split()[-1]]
        ep = hits[0]["identifier"] if len(hits) == 1 else None
        if (fixed := EP_IDS.get((row["year"], row["region"], row["name"]))) is not None:
            at = taken[(row["year"], row["region"], row["name"])] = taken.get((row["year"], row["region"], row["name"]), -1) + 1
            ep = fixed[at]
            if len(fixed) > 1:
                row = {**row, "round": None}
        listed = next((m for m in candidates if m["identifier"] == ep), None)
        ep_name = f"{listed.get('givenName', '')} {listed.get('familyName', '')}".strip() if listed else None
        party = row["party"]
        if party is None and ep is not None:
            detail = json.loads(raw.cached(SOURCE, f"{API}/meps/{ep}?{FORMAT}").path.read_text())["data"][0]
            start = f"{row['year']}-07"
            for membership in detail["hasMembership"]:
                if membership.get("membershipClassification") == NATIONAL and membership["memberDuring"]["startDate"].startswith(start):
                    body = json.loads(
                        raw.cached(SOURCE, f"{API}/corporate-bodies/{membership['organization'].split('/')[1]}?{FORMAT}").path.read_text()
                    )["data"][0]
                    party = f"EP:{body['label']}"
        matched.append({**row, "party": party, "ep": ep, "ep_name": ep_name, "qid": wikidata[ep][0] if ep in wikidata else None, "mnis": wikidata[ep][1] if ep in wikidata else None, "wd_name": wikidata[ep][2] if ep in wikidata else None})
    elected = pl.DataFrame(matched, infer_schema_length=None)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "results.parquet")
    elected.write_parquet(PARSED / "members.parquet")
    print(frame.filter(pl.col("unit").is_null()).group_by("year").agg(pl.col("region").n_unique(), pl.len()).sort("year"))
    print(elected.group_by("year").agg(pl.len(), pl.col("ep").null_count().alias("no_ep"), pl.col("qid").null_count().alias("no_qid"), pl.col("party").null_count().alias("no_party")).sort("year"))
    print("unmatched:", elected.filter(pl.col("ep").is_null()).select("year", "region", "name").rows())
    labels = set(frame.filter(pl.col("kind") == "option")["label"]) | {p for p in elected["party"] if p}
    print("unmapped labels:", sorted(l for l in labels if l not in PARTIES and l.removeprefix("EP:") not in NATIONAL_PARTIES and l not in ("Others", "SNP/PC")))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "results.parquet")
    elected = pl.read_parquet(PARSED / "members.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="European Parliament results in Great Britain 1999–2019 by electoral region, from the Commons Library's "
            "papers, the European Parliament's UK Office, and the London Regional Returning Officer's 2019 declaration, "
            "with the MEPs declared elected; the Women's Equality Party recorded twice is merged into one",
            polity="GB",
        )
        cs.merge(cs.key("gb.party", "womens-equality-party"), cs.key("gb.party", "women-s-equality-party"))
        sources: dict[int | str, UUID] = {}
        for key, (url, publisher, title) in {
            **{year: (url, LIBRARY, title) for year, (url, title) in PAPERS.items()},
            2004: EUROPARL,
            "london": LONDON,
        }.items():
            sources[key] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[key],
                kind="official_body",
                publisher=publisher,
                title=title,
                url=url.split("id_/", 1)[-1],
            )
            cs.artifact(raw.cached(SOURCE, url), sources[key])
        ep_source = cs.source("European Parliament", "Open Data Portal: Members of the European Parliament")
        cs.add(
            "source",
            id=ep_source,
            kind="official_body",
            publisher="European Parliament",
            title="Open Data Portal: Members of the European Parliament",
            url="https://data.europarl.europa.eu/",
            license="CC BY 4.0",
        )
        parties: dict[str, UUID] = {}
        for key in {*PARTIES.values(), *NATIONAL_PARTIES.values()}:
            parties[key] = cs.key(*EXISTING[key]) if key in EXISTING else cs.key("gb.party", key)
            if parties[key] in cs.minted:
                if key not in NEW_PARTIES:
                    raise ValueError(f"{key} is not in the database")
                cs.add("party", id=parties[key], kind="party", polity_id=cs.polity("GB"))
                cs.add("name", entity_id=parties[key], text=NEW_PARTIES[key], lang="en", kind="official", is_preferred=True)
        known = {
            (scheme, value): entity
            for scheme, value, entity in conn.execute(
                """select scheme, value, entity_id from external_id x
                   where scheme in ('wikidata', 'mnis', 'ep.mep')
                     and not exists (select from entity_merge m where m.entity_id = x.entity_id)"""
            ).fetchall()
        }
        pcons = {
            folded(name.replace("&", "and")): unit
            for name, unit in conn.execute(
                """select distinct n.text, u.id from contest c
                   join election e on e.id = c.election_id and e.date_from = '1997-05-01'
                   join polity p on p.id = e.polity_id and p.iso_code = 'GB'
                   join name n on n.entity_id = c.district_area_id and n.is_preferred and n.lang = 'en'
                   join result_set r on r.contest_id = c.id and r.is_default
                   join lateral (select v.unit_k from result_value v where v.result_set_k = r.k limit 1) v on true
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'gb.pcon'"""
            ).fetchall()
        }
        persons: dict[str, UUID] = {}
        owned: dict[UUID, list[tuple[UUID, str]]] = {}
        for person, id, text in conn.execute(
            """select p.id, n.id, n.text from person p join changeset c on c.id = p.changeset_id and c.pipeline = %s
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'""",
            [SOURCE],
        ).fetchall():
            owned.setdefault(person, []).append((id, text))
        rounds = elected.filter(pl.col("round").is_not_null()).height
        for (year, region), lines in frame.sort("year").group_by("year", "region", maintain_order=True):
            contest_key = f"GB:{year}:gb.ep_constituency:{region}"
            contest = cs.key("eu.contest", contest_key)
            if contest in cs.minted:
                raise ValueError(f"no contest {contest_key}")
            (election, area) = conn.execute(
                "select election_id, district_area_id from contest where id = %s", [contest]
            ).fetchone()
            (unit,) = conn.execute(
                "select id from reporting_unit where area_id = %s and area_relation = 'equals' limit 1", [area]
            ).fetchone()
            existing = conn.execute(
                "select id, kind, party_id from contest_option where contest_id = %s", [contest]
            ).fetchall()
            by_party = {party: id for id, kind, party in existing if party is not None and kind == "list"}
            others = next((id for id, kind, _ in existing if kind == "others"), None)
            source = sources["london" if (year, region) == (2019, "london") else year]
            options: dict[str, UUID] = {}
            position = 0

            def option_of(label: str) -> UUID:
                nonlocal position, others
                if label in options:
                    return options[label]
                key = SNP_PC[region] if label == "SNP/PC" else PARTIES.get(label)
                if label == "Others":
                    if others is None:
                        others = cs.key(f"{SOURCE}.option", f"{year}:{region}:others")
                        cs.add("contest_option", id=others, contest_id=contest, kind="others", ballot_label="Others")
                    options[label] = others
                elif key is not None and parties[key] in by_party:
                    options[label] = by_party[parties[key]]
                else:
                    position += 1
                    option = options[label] = cs.key(f"{SOURCE}.option", f"{year}:{region}:{slug(label)}")
                    independent = key is None
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate" if independent else "list",
                        party_id=None if independent else parties[key],
                        ballot_label=label,
                    )
                    if independent:
                        text = re.sub(r"^Independent (?:\((.*)\)|- (.*))$", lambda found: found[1] or found[2], label)
                        person = cs.key(f"{SOURCE}.person", f"{year}:{region}:{slug(text)}")
                        if person in cs.minted:
                            cs.add("person", id=person)
                            cs.add("name", entity_id=person, text=text, lang="en", kind="official", is_preferred=True)
                        cs.add(
                            "candidacy",
                            id=cs.key(f"{SOURCE}.candidacy", f"{year}:{region}:{slug(text)}"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=text,
                            is_independent=True,
                        )
                return options[label]

            def value(result_set: UUID, at: UUID, option: UUID | None, measure: int, amount: int) -> None:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=at,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=amount,
                )

            result_set = cs.key(f"{SOURCE}.result_set", f"{year}:{region}")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=1,
            )
            cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
            measures = {"electorate": REGISTERED, "ballots": BALLOTS, "valid": VALID, "rejected": INVALID}
            regional = lines.filter(pl.col("unit").is_null())
            constituencies = lines.filter(pl.col("unit").is_not_null())
            totals = dict(regional.filter(pl.col("kind") != "option").select("kind", "value").iter_rows())
            if year == 1999:
                summed = constituencies.filter(pl.col("kind") == "option").group_by("label", maintain_order=True).agg(pl.col("value").sum())
                votes = list(summed.iter_rows())
                valid = int(summed["value"].sum())
                printed = totals["printed_valid"]
                totals = {"electorate": totals["electorate"], "valid": valid} if valid == printed else {"valid": valid}
                if valid != printed:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md=f"The European Parliament's UK Office printed {printed:,} votes for this region in 1999, "
                        "which are another region's figures, so its 1999 electorate for the region is not used either.",
                    )
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="Regional totals are the sums of the Commons Library's figures by Westminster constituency "
                    "(Research Paper 99/64, Table 11), which match the regional totals the European Parliament's UK Office "
                    "printed; the Library's regional table rounds them to thousands. The electorate is the UK Office's. "
                    "The Library groups every party but the Conservatives, Labour, the Liberal Democrats, the SNP, Plaid "
                    "Cymru, the Greens, and UKIP as Others.",
                )
            else:
                votes = list(regional.filter(pl.col("kind") == "option").select("label", "value").iter_rows())
                if year != 2004 and (year, region) != (2019, "london"):
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="caveat",
                        body_md="The Commons Library groups smaller parties and independent candidates as Others.",
                    )
            if "valid" not in totals:
                totals["valid"] = sum(amount for _, amount in votes)
            if (lined := sum(amount for _, amount in votes)) != totals["valid"]:
                print(f"{year} {region}: lines sum to {lined:,}, valid {totals['valid']:,}")
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"The source's party and candidate lines add to {lined:,}, not its printed total of "
                    f"{totals['valid']:,} valid votes. Both are stored as printed.",
                )
            if "rejected" in totals and "ballots" in totals and totals["valid"] + totals["rejected"] != totals["ballots"]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=f"The declaration's valid votes ({totals['valid']:,}) and rejected ballot papers "
                    f"({totals['rejected']:,}) add to {totals['valid'] + totals['rejected']:,}, not its printed total of "
                    f"{totals['ballots']:,} ballot papers. All three figures are stored as printed.",
                )
            for kind, amount in totals.items():
                value(result_set, unit, None, measures[kind], amount)
            for label, amount in votes:
                value(result_set, unit, option_of(label), VOTES, amount)
            members = elected.filter((pl.col("year") == year) & (pl.col("region") == region))
            seats: dict[UUID, int] = {}
            for member in members.iter_rows(named=True):
                label = member["party"].removeprefix("EP:")
                key = (
                    SNP_PC[region]
                    if label == "SNP/PC"
                    else NATIONAL_PARTIES.get(label)
                    if member["party"].startswith("EP:")
                    else PARTIES.get(label)
                )
                option = next(
                    (options[l] for l in options if (SNP_PC[region] if l == "SNP/PC" else PARTIES.get(l)) == key), None
                )
                if key is None or option is None:
                    raise ValueError(f"{year} {region}: {member['name']} elected for {member['party']!r}, which has no line")
                seats[option] = seats.get(option, 0) + 1
                ident = member["qid"] or member["ep"] or f"{year}:{region}:{slug(member['name'])}"
                if ident not in persons:
                    held = [
                        (scheme, value)
                        for scheme, value in (("wikidata", member["qid"]), ("mnis", member["mnis"]), ("ep.mep", member["ep"]))
                        if value is not None
                    ]
                    person = next((known[id] for id in held if id in known), None)
                    keyed = []
                    if person is None:
                        keyed = held[:1]
                        person = cs.key(*held[0]) if held else cs.key(f"{SOURCE}.person", ident)
                        cs.add("person", id=person)
                    if person in cs.minted or person in owned:
                        text = next(
                            text
                            for text in (member["wd_name"], member["ep_name"], member["name"])
                            if text is not None and not re.search(r",|\b[A-Z]{2,}\b|\bMc[a-z]", text)
                        )
                        cs.add("name", entity_id=person, text=text, lang="en", kind="official", is_preferred=True)
                        for id, old in owned.get(person, []):
                            if old != text:
                                cs.add("name", id=id, entity_id=person, text=old, lang="en", kind="official", is_preferred=False)
                    for id in held:
                        if id not in known:
                            known[id] = person
                            if id not in keyed:
                                cs.add("external_id", entity_id=person, scheme=id[0], value=id[1])
                    persons[ident] = person
                candidacy = cs.key(f"{SOURCE}.candidacy", f"{year}:{region}:{member['ep'] or slug(member['name'])}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=persons[ident],
                    option_id=option,
                    role="list_member",
                    ballot_name=member["name"],
                    member_of_party_id=parties[key],
                    is_independent=False,
                )
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    candidacy_id=candidacy,
                    kind="elected",
                    notes=f"Elected in round {member['round']} of the allocation" if member["round"] else None,
                )
            printed = dict(regional.filter(pl.col("seats").is_not_null()).select("label", "seats").iter_rows())
            for label, count in printed.items():
                if count != seats.get(options[label], 0):
                    raise ValueError(f"{year} {region}: {label} won {count} seats, {seats.get(options[label], 0)} members named")
            for option, count in seats.items():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=count)
            cs.add(
                "note",
                entity_id=result_set,
                kind="caveat",
                body_md="The source names only the members elected from each list, not the full ranked lists, so list "
                "ranks and the members below each cut line are not recorded.",
            )
            if year in (2014, 2019) and region != "london":
                cs.add(
                    "citation",
                    entity_id=result_set,
                    source_id=ep_source,
                )
            if year == 1999:
                pcon_set = cs.key(f"{SOURCE}.result_set", f"{year}:{region}:pcon")
                cs.add(
                    "result_set",
                    id=pcon_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=False,
                    completeness=1,
                )
                for (name,), rows in constituencies.group_by("unit", maintain_order=True):
                    for row in rows.iter_rows(named=True):
                        if row["kind"] == "option":
                            value(pcon_set, pcons[folded(name.replace("&", "and"))], option_of(row["label"]), VOTES, row["value"])
                        else:
                            value(pcon_set, pcons[folded(name.replace("&", "and"))], None, VALID, row["value"])
        print(f"{rounds} members with a round, {len(persons)} persons")
        return cs.write()
