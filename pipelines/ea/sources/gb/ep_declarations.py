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
VERSION = "4"
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
DECLARATIONS = {
    "north-east": (
        "https://www.middlesbrough.gov.uk/media/w4nodjdl/european-parliamentary-election-statement-of-result-north-east-may-2019.pdf",
        ".pdf",
    ),
    "north-west": (
        "https://www.ribblevalley.gov.uk/downloads/file/601/statement-of-the-result-for-the-north-west-electoral-region",
        ".pdf",
    ),
    "yorkshire-and-the-humber": (
        "https://datamillnorth.org/download/24lmx/dba6deb5-28b5-4b36-ad83-ada33db2a325/EPE%202019%20YH%20Totals%20by%20Authority.csv",
        ".csv",
    ),
    "east-midlands": (
        "https://www.mansfield.gov.uk/downloads/file/731/european-results-for-east-midlands-2019",
        ".pdf",
    ),
    "west-midlands": (
        "https://www.birmingham.gov.uk/info/50381/past_election_results/1946/european_parliamentary_election_results_may_2019",
        ".html",
    ),
    "eastern": (
        "https://www.maldon.gov.uk/download/downloads/id/17700/european_election_23_may_2019_-_east_of_england_-_statement_under_rule_61_1.pdf",
        ".pdf",
    ),
    "south-east": ("https://www.eastleigh.gov.uk/media/5216/declaration-of-result-european-2019.pdf", ".pdf"),
    "south-west": (
        "https://www.bcpcouncil.gov.uk/news-hub/news-articles/european-election-results-for-the-south-west",
        ".html",
    ),
    "wales": (
        "https://www.rctcbc.gov.uk/EN/Council/Votingandelections/RelatedDocuments/DeclarationofResults/2019/DeclarationofresultElectoralRegionofWales.pdf",
        ".pdf",
    ),
    "scotland": ("https://www.emb.scot/downloads/file/343/statement-of-results-for-scotland", ".pdf"),
}
SCANNED = {"north-west", "wales"}
GROUPED = "The Commons Library groups smaller parties and independent candidates as Others."
UNLISTED = (
    "List ranks are from the Regional Returning Officer's Statement of Parties and Individual Candidates Nominated. "
    "The lists of parties the source groups as Others are not recorded, because their votes are not given separately."
)
DC = "https://candidates.democracyclub.org.uk"
SOPN_DATES = {2014: "2014-05-22", 2019: "2019-05-23"}
DC_PARTIES = {
    "PP52": "conservative",
    "PP53": "labour",
    "PP90": "liberal-democrats",
    "PP63": "green-party",
    "PP130": "scottish-greens",
    "PP85": "uk-independence-party",
    "PP102": "scottish-national-party",
    "PP77": "plaid-cymru",
    "PP106": "british-national-party",
    "PP7931": "reform-uk",
    "PP9077": "the-independent-group-for-change",
    "PP17": "english-democrats",
    "PP616": "animal-welfare-party",
    "PP9060": "uk-european-union-party",
    "PP2755": "women-s-equality-party",
    "PP79": "christian-peoples-alliance",
    "PP73": "socialist-labour-party",
    "PP2164": "no2eu",
    "PP1918": "an-independence-from-europe",
    "PP1931": "national-health-action-party",
    "PP2214": "britain-first",
    "PP2055": "the-yorkshire-party",
    "PP2120": "yorkshire-first",
    "PP32": "national-liberal-party-true-liberalism",
    "PP133": "the-peace-party-non-violence-justice-environment",
    "PP2342": "the-roman-party-ave",
    "PP770": "pirate-party-uk",
    "PP110": "the-socialist-party-of-great-britain",
    "PP1951": "independent-network",
}
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
REGION_LABELS = {
    "east": "eastern",
    "yorkshire-the-humber": "yorkshire-and-the-humber",
    "yorkshire-and-humber": "yorkshire-and-the-humber",
}
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
    "The Yorkshire Party": "the-yorkshire-party",
    "Independent Network": "independent-network",
    "The Socialist Party of Great Britain": "the-socialist-party-of-great-britain",
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
RENAMED = {(2019, "south-east", "Philips")}
UNRANKED = (
    "The source names only the members elected from each list, not the full ranked lists, so list ranks and the "
    "members below each cut line are not recorded."
)
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
LINE = re.compile(r"^\s*(\S.*?\S)\s{3,}([\d,]+)\s*$")


def number(text: str) -> int:
    return int(text.replace(",", ""))


def folded(text: str) -> str:
    return re.sub(
        r"[^a-z]+", " ", unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()
    ).strip()


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
                if error.response.status_code not in (429,) and error.response.status_code < 500 or attempt == 4:
                    raise
        raise AssertionError


def ballots_url(date: str) -> str:
    return f"{DC}/api/next/ballots/?election_id=europarl.{date}&page_size=50"


def export_url(date: str) -> str:
    fields = "".join(f"&extra_fields={field}" for field in ("wikidata_id", "mnis_id", "party_list_position", "elected"))
    return f"{DC}/data/export_csv/?election_id=europarl.{date}{fields}"


def gb_ballots(date: str) -> list[dict]:
    return [
        ballot
        for ballot in json.loads(raw.cached(SOURCE, ballots_url(date)).path.read_text())["results"]
        if ballot["post"]["slug"] != "uk-northern-ireland"
    ]


def sopn(ballot: dict) -> str:
    for url in (ballot["sopn"]["source_url"], ballot["sopn"]["uploaded_file"]):
        try:
            if raw.cached(SOURCE, url).path.read_bytes()[:4] == b"%PDF":
                return url
        except LookupError:
            continue
    raise LookupError(f"no SoPN for {ballot['ballot_paper_id']}")


def party_key(label: str, region: str) -> str | None:
    key = (
        SNP_PC[region]
        if label == "SNP/PC"
        else NATIONAL_PARTIES.get(label[3:])
        if label.startswith("EP:")
        else PARTIES.get(label)
    )
    return "scottish-greens" if region == "scotland" and key == "green-party" else key


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
    for url, suffix in DECLARATIONS.values():
        get(url, suffix)
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
    for date in SOPN_DATES.values():
        get(ballots_url(date))
        get(export_url(date), ".csv")
        for ballot in gb_ballots(date):
            for url in (ballot["sopn"]["source_url"], ballot["sopn"]["uploaded_file"]):
                try:
                    if get(url, ".pdf").path.read_bytes()[:4] == b"%PDF":
                        break
                except httpx.HTTPError:
                    continue


def region_of(label: str) -> str:
    key = slug(label.replace("&", "and"))
    return REGION_LABELS.get(key, key)


def parse() -> None:
    lines: list[dict[str, object]] = []
    members: list[dict[str, object]] = []

    def line(
        year: int, region: str, label: str, kind: str, value: int, unit: str | None = None, seats: int | None = None
    ) -> None:
        lines.append(
            {"year": year, "region": region, "unit": unit, "label": label, "kind": kind, "value": value, "seats": seats}
        )

    text = pdf(PAPERS[1999][0])
    start = text.index("Votes cast by parliamentary constituency")
    table = text[start : text.index("Table 12", start)]
    region = None
    columns = {
        7: ("Con", "Lab", "LDem", "Green", "UKIP", "Others"),
        8: ("Con", "Lab", "LDem", "SNP/PC", "Green", "UKIP", "Others"),
    }
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
            members.append(
                {"year": 1999, "region": row["region"], "round": None, "name": row["name"], "party": row["party"]}
            )

    page = raw.cached(SOURCE, EUROPARL[0]).path.read_text(encoding="latin-1")
    order = iter(
        [
            "eastern",
            "east-midlands",
            "london",
            "north-east",
            "north-west",
            "south-east",
            "south-west",
            "west-midlands",
            "yorkshire-and-the-humber",
            "scotland",
            "wales",
            None,
        ]
    )
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
            members.append(
                {"year": 2004, "region": region, "round": int(cells[0][:-1]), "name": cells[1], "party": cells[2]}
            )
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
                            {
                                "year": year,
                                "region": region,
                                "round": int(elected[1]),
                                "name": elected[2],
                                "party": elected[3],
                            }
                        )
            elif found := re.match(r"\s+Summary: (.+)$", row):
                region = region_of(found[1].strip())
                if region not in REGIONS or (year, region) == (2019, "london"):
                    continue
                for following in rows[at + 1 : at + 25]:
                    if total := re.match(r"^\s*Total\s+([\d,]+)", following):
                        line(year, region, "valid", "valid", number(total[1]))
                        break
                    if party := re.match(
                        r"^\s+(\S.*?\S)\s{2,}([\d,]{3,})\s+[\d.]+%(?:\s+[-+][\d.]+%)?\s+(\d+)(?:\s+(?:[-+]\d+|-))?\s*$",
                        following,
                    ):
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

    seats = {
        (entry["region"], party_key(str(entry["label"]), str(entry["region"]))): entry["seats"]
        for entry in lines
        if entry["year"] == 2019
        and entry["region"] in DECLARATIONS
        and entry["kind"] == "option"
        and isinstance(entry["seats"], int)
    }
    lines = [
        entry
        for entry in lines
        if not (entry["year"] == 2019 and entry["region"] in DECLARATIONS and entry["kind"] == "option")
    ]
    with open(Path(__file__).with_name("ep_declarations_2019.tsv"), newline="") as handle:
        declared = list(csv.DictReader(handle, delimiter="\t"))
    for region, (url, suffix) in DECLARATIONS.items():
        rows = [row for row in declared if row["region"] == region]
        if suffix == ".csv":
            with open(raw.cached(SOURCE, url).path, newline="", encoding="utf-8-sig") as handle:
                printed = {cell for entry in csv.reader(handle) for cell in entry}
        elif suffix == ".pdf":
            printed = set(re.findall(r"\d[\d,.]*\d", pdf(url)))
        else:
            printed = set(re.findall(r"\d[\d,.]*\d", raw.cached(SOURCE, url).path.read_text(errors="ignore")))
        printed = {re.sub(r"[,.]", "", figure) for figure in printed}
        missing = [row["label"] for row in rows if row["votes"] not in printed]
        if missing and region not in SCANNED:
            raise ValueError(f"2019 {region}: {missing} not in the declaration")
        valid = next(
            entry["value"]
            for entry in lines
            if (entry["year"], entry["region"], entry["kind"]) == (2019, region, "valid")
        )
        if (summed := sum(int(row["votes"]) for row in rows)) != valid:
            raise ValueError(f"2019 {region}: declared lines sum to {summed:,}, the Library prints {valid:,}")
        for row in rows:
            key = party_key(row["label"], region)
            line(
                2019, region, row["label"], "option", int(row["votes"]), seats=seats.get((region, key), 0) if key else 0
            )

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
        hits = (
            [m for m in candidates if folded(f"{m.get('givenName', '')} {m.get('familyName', '')}") == name]
            or [
                m
                for m in candidates
                if folded(m.get("familyName", "")) == name.split()[-1]
                and folded(m.get("givenName", ""))[:1] == name[:1]
            ]
            or [m for m in candidates if folded(m.get("familyName", "")) == name.split()[-1]]
        )
        ep = hits[0]["identifier"] if len(hits) == 1 else None
        if (fixed := EP_IDS.get((row["year"], row["region"], row["name"]))) is not None:
            at = taken[(row["year"], row["region"], row["name"])] = (
                taken.get((row["year"], row["region"], row["name"]), -1) + 1
            )
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
                if membership.get("membershipClassification") == NATIONAL and membership["memberDuring"][
                    "startDate"
                ].startswith(start):
                    body = json.loads(
                        raw.cached(
                            SOURCE, f"{API}/corporate-bodies/{membership['organization'].split('/')[1]}?{FORMAT}"
                        ).path.read_text()
                    )["data"][0]
                    party = f"EP:{body['label']}"
        matched.append(
            {
                **row,
                "party": party,
                "ep": ep,
                "ep_name": ep_name,
                "qid": wikidata[ep][0] if ep in wikidata else None,
                "mnis": wikidata[ep][1] if ep in wikidata else None,
                "wd_name": wikidata[ep][2] if ep in wikidata else None,
            }
        )
    elected = pl.DataFrame(matched, infer_schema_length=None)

    def same(row: dict[str, str], name: str) -> bool:
        printed = folded(f"{row['forenames']} {row['surname']} {row['commonly_known_as']}").split()
        held = folded(name).split()
        return (
            held[-1] in printed or (folded(row["surname"]) or printed[-1]).split()[-1] in held or held[0] == printed[0]
        )

    with open(Path(__file__).with_name("ep_sopn.tsv"), newline="") as handle:
        printed = list(csv.DictReader(handle, delimiter="\t"))
    lists = []
    for year, date in SOPN_DATES.items():
        export = list(csv.DictReader(raw.cached(SOURCE, export_url(date)).path.read_text().splitlines()))
        for ballot in gb_ballots(date):
            region = ballot["post"]["slug"].removeprefix("uk-")
            known: dict[str, list[dict[str, str]]] = {}
            for row in export:
                if row["ballot_paper_id"] == ballot["ballot_paper_id"]:
                    known.setdefault(row["party_id"], []).append(row)
            groups: dict[str, list[dict[str, str]]] = {}
            for row in printed:
                if int(row["year"]) == year and row["region"] == region and not row["remark"]:
                    groups.setdefault(row["party_printed"], []).append(row)
            if sum(map(len, groups.values())) != sum(map(len, known.values())):
                raise ValueError(
                    f"{year} {region}: {sum(map(len, groups.values()))} printed, DC lists {sum(map(len, known.values()))}"
                )
            for party, rows in groups.items():
                if party == "Independent":
                    pairs = [
                        (row, next(d for d in known["ynmp-party:2"] if same(row, d["person_name"]))) for row in rows
                    ]
                else:
                    dc_party = max(
                        (id for id in known if id != "ynmp-party:2"),
                        key=lambda id: sum(
                            any(
                                folded(d["person_name"]).split()[-1]
                                in folded(f"{row['surname']} {row['commonly_known_as']}").split()
                                for d in known[id]
                            )
                            for row in rows
                        ),
                    )
                    by_position = {int(d["party_list_position"] or 1): d for d in known[dc_party]}
                    if len(by_position) != len(rows) or [int(row["position"]) for row in rows] != list(
                        range(1, len(rows) + 1)
                    ):
                        raise ValueError(f"{year} {region} {party}: positions do not run 1 to {len(rows)}")
                    pairs = [(row, by_position[int(row["position"])]) for row in rows]
                for row, dc in pairs:
                    if not same(row, dc["person_name"]) and (year, region, row["surname"]) not in RENAMED:
                        raise ValueError(
                            f"{year} {region} {party} {row['position']}: {row} against {dc['person_name']}"
                        )
                    lists.append(
                        {
                            "year": year,
                            "region": region,
                            "dc_party": dc["party_id"],
                            "position": int(row["position"]) if row["position"] else None,
                            "ballot_name": row["commonly_known_as"] or f"{row['forenames']} {row['surname']}",
                            "dc_person": dc["person_id"],
                            "dc_name": dc["person_name"],
                            "qid": dc["wikidata_id"].strip('"') or None,
                            "mnis": dc["mnis_id"] or None,
                            "elected": dc["elected"] == "t",
                            "sopn": sopn(ballot),
                        }
                    )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(lists, infer_schema_length=None).write_parquet(PARSED / "lists.parquet")
    frame.write_parquet(PARSED / "results.parquet")
    elected.write_parquet(PARSED / "members.parquet")
    print(
        frame.filter(pl.col("unit").is_null()).group_by("year").agg(pl.col("region").n_unique(), pl.len()).sort("year")
    )
    print(
        elected.group_by("year")
        .agg(
            pl.len(),
            pl.col("ep").null_count().alias("no_ep"),
            pl.col("qid").null_count().alias("no_qid"),
            pl.col("party").null_count().alias("no_party"),
        )
        .sort("year")
    )
    print("unmatched:", elected.filter(pl.col("ep").is_null()).select("year", "region", "name").rows())
    labels = set(frame.filter(pl.col("kind") == "option")["label"]) | {p for p in elected["party"] if p}
    print(
        "unmapped labels:",
        sorted(
            l
            for l in labels
            if l not in PARTIES and l.removeprefix("EP:") not in NATIONAL_PARTIES and l not in ("Others", "SNP/PC")
        ),
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "results.parquet")
    elected = pl.read_parquet(PARSED / "members.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="European Parliament results in Great Britain 1999–2019 by electoral region, from the Commons Library's "
            "papers, the European Parliament's UK Office, and the Regional Returning Officers' 2019 declarations, "
            "with the MEPs declared elected; the Women's Equality Party recorded twice is merged into one",
            polity="GB",
        )
        cs.merge(cs.key("gb.party", "womens-equality-party"), cs.key("gb.party", "women-s-equality-party"))
        sources: dict[int | str, UUID] = {}
        for key, (url, publisher, title) in {
            **{year: (url, LIBRARY, title) for year, (url, title) in PAPERS.items()},
            2004: EUROPARL,
            "london": LONDON,
            **{
                region: (
                    url,
                    f"Regional Returning Officer, {named}",
                    f"Declaration of result, European Parliamentary Election, 23 May 2019, {named}",
                )
                for region, (url, _) in DECLARATIONS.items()
                for named in [
                    f"Electoral Region of {REGIONS[region]}"
                    if region in ("wales", "scotland")
                    else f"{REGIONS[region]} Region"
                ]
            },
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
        for key in {*PARTIES.values(), *NATIONAL_PARTIES.values(), *DC_PARTIES.values()}:
            parties[key] = cs.key(*EXISTING[key]) if key in EXISTING else cs.key("gb.party", key)
            if parties[key] in cs.minted:
                if key not in NEW_PARTIES:
                    raise ValueError(f"{key} is not in the database")
                cs.add("party", id=parties[key], kind="party", polity_id=cs.polity("GB"))
                cs.add(
                    "name", entity_id=parties[key], text=NEW_PARTIES[key], lang="en", kind="official", is_preferred=True
                )
        known = {
            (scheme, value): entity
            for scheme, value, entity in conn.execute(
                """select scheme, value, entity_id from external_id x
                   where scheme in ('wikidata', 'mnis', 'ep.mep', 'democracyclub.person')
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
        lists = pl.read_parquet(PARSED / "lists.parquet")
        ranked = 0
        stale: dict[UUID, list[UUID]] = {}
        for note, entity in conn.execute(
            """select n.id, n.entity_id from note n join changeset c on c.id = n.changeset_id and c.pipeline = %s
               where n.body_md = %s""",
            [SOURCE, UNRANKED],
        ).fetchall():
            stale.setdefault(entity, []).append(note)
        grouped: dict[UUID, list[tuple[UUID, str]]] = {}
        for note, entity, body in conn.execute(
            """select n.id, n.entity_id, n.body_md from note n join changeset c on c.id = n.changeset_id
               and c.pipeline = %s where n.body_md = any(%s)""",
            [SOURCE, [GROUPED, UNLISTED]],
        ).fetchall():
            grouped.setdefault(entity, []).append((note, body))
        own_others: dict[UUID, list[UUID]] = {}
        for option, contest in conn.execute(
            """select o.id, o.contest_id from contest_option o join changeset c on c.id = o.changeset_id
               where c.pipeline = %s and o.kind = 'others'
                 and not exists (select from result_value v where v.option_k = o.k)""",
            [SOURCE],
        ).fetchall():
            own_others.setdefault(contest, []).append(option)
        sopns: dict[str, UUID] = {}
        for year, date in SOPN_DATES.items():
            for ballot in gb_ballots(date):
                url = sopn(ballot)
                region = REGIONS[ballot["post"]["slug"].removeprefix("uk-")]
                publisher = f"Regional Returning Officer, {region} Region"
                title = f"Statement of Parties and Individual Candidates Nominated, European Parliamentary Election, {date}, {region}"
                sopns[url] = cs.source(publisher, title)
                cs.add(
                    "source",
                    id=sopns[url],
                    kind="official_body",
                    publisher=publisher,
                    title=title,
                    url=re.sub(r"^https://web\.archive\.org/web/\d+(?:id_)?/", "", ballot["sopn"]["source_url"]),
                )
                cs.artifact(raw.cached(SOURCE, url), sopns[url])
        dc_source = cs.source("Democracy Club", "Candidates: European Parliament elections 2014 and 2019")
        cs.add(
            "source",
            id=dc_source,
            kind="community",
            publisher="Democracy Club",
            title="Candidates: European Parliament elections 2014 and 2019",
            url=f"{DC}/elections/europarl.2019-05-23/",
            license="CC BY 4.0",
        )
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
            declared = year == 2019 and (region == "london" or region in DECLARATIONS)
            source = sources[region if declared else year]
            options: dict[str, UUID] = {}
            position = 0

            def option_of(label: str) -> UUID:
                nonlocal position, others
                if label in options:
                    return options[label]
                key = party_key(label, region)
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
                summed = (
                    constituencies.filter(pl.col("kind") == "option")
                    .group_by("label", maintain_order=True)
                    .agg(pl.col("value").sum())
                )
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
                if year != 2004 and not declared:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=GROUPED)
                for note, body in grouped.get(result_set, []) if declared else []:
                    if body != GROUPED:
                        continue
                    cs.add(
                        "note_drop",
                        note_id=note,
                        entity_id=result_set,
                        body_md=body,
                        reason="The Regional Returning Officer's declaration gives every party and candidate's votes.",
                    )
                for option in own_others.get(contest, []) if declared else []:
                    cs.add(
                        "option_drop",
                        option_id=option,
                        contest_id=contest,
                        reason="The Regional Returning Officer's declaration gives every party and candidate's votes.",
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
            if (
                "rejected" in totals
                and "ballots" in totals
                and totals["valid"] + totals["rejected"] != totals["ballots"]
            ):
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
            listed = lists.filter((pl.col("year") == year) & (pl.col("region") == region)).to_dicts()
            used: set[int] = set()
            unlisted: set[str] = set()
            seats: dict[UUID, int] = {}
            for member in members.iter_rows(named=True):
                key = party_key(member["party"], region)
                option = next((options[l] for l in options if party_key(l, region) == key), None)
                if key is None or option is None:
                    raise ValueError(
                        f"{year} {region}: {member['name']} elected for {member['party']!r}, which has no line"
                    )
                seats[option] = seats.get(option, 0) + 1
                surname = folded(member["name"]).split()[-1]
                match = next(
                    (
                        index
                        for index, row in enumerate(listed)
                        if index not in used
                        and DC_PARTIES.get(row["dc_party"]) == key
                        and (
                            (member["qid"] is not None and row["qid"] == member["qid"])
                            or surname in (folded(row["dc_name"]).split()[-1], folded(row["ballot_name"]).split()[-1])
                        )
                    ),
                    None,
                )
                if listed and match is None:
                    raise ValueError(f"{year} {region}: {member['name']} is on no list")
                row = listed[match] if match is not None else None
                if match is not None:
                    used.add(match)
                ident = member["qid"] or member["ep"] or f"{year}:{region}:{slug(member['name'])}"
                if ident not in persons:
                    held = [
                        (scheme, value)
                        for scheme, value in (
                            ("wikidata", member["qid"]),
                            ("mnis", member["mnis"]),
                            ("ep.mep", member["ep"]),
                        )
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
                                cs.add(
                                    "name",
                                    id=id,
                                    entity_id=person,
                                    text=old,
                                    lang="en",
                                    kind="official",
                                    is_preferred=False,
                                )
                    for id in held:
                        if id not in known:
                            known[id] = person
                            if id not in keyed:
                                cs.add("external_id", entity_id=person, scheme=id[0], value=id[1])
                    persons[ident] = person
                if row is not None and ("democracyclub.person", row["dc_person"]) not in known:
                    known[("democracyclub.person", row["dc_person"])] = persons[ident]
                    cs.add(
                        "external_id", entity_id=persons[ident], scheme="democracyclub.person", value=row["dc_person"]
                    )
                candidacy = cs.key(f"{SOURCE}.candidacy", f"{year}:{region}:{member['ep'] or slug(member['name'])}")
                cs.add(
                    "candidacy",
                    id=candidacy,
                    person_id=persons[ident],
                    option_id=option,
                    role="list_member",
                    list_position=row["position"] if row is not None else None,
                    ballot_name=row["ballot_name"] if row is not None else member["name"],
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
                    raise ValueError(
                        f"{year} {region}: {label} won {count} seats, {seats.get(options[label], 0)} members named"
                    )
            for option, count in seats.items():
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=count)
            for index, row in enumerate(listed):
                if index in used or row["dc_party"] == "ynmp-party:2":
                    continue
                if row["elected"]:
                    raise ValueError(f"{year} {region}: {row['ballot_name']} elected, but no member matched")
                key = DC_PARTIES.get(row["dc_party"])
                option = next((options[l] for l in options if key is not None and party_key(l, region) == key), None)
                if option is None:
                    unlisted.add(row["dc_party"])
                    continue
                ids = [
                    (scheme, value)
                    for scheme, value in (
                        ("wikidata", row["qid"]),
                        ("mnis", row["mnis"]),
                        ("democracyclub.person", row["dc_person"]),
                    )
                    if value is not None
                ]
                person = next((known[id] for id in ids if id in known), None)
                keyed = []
                if person is None:
                    keyed = ids[:1]
                    person = cs.key(*ids[0])
                    cs.add("person", id=person)
                    if person in cs.minted:
                        cs.add(
                            "name", entity_id=person, text=row["dc_name"], lang="en", kind="official", is_preferred=True
                        )
                for id in ids:
                    if id not in known:
                        known[id] = person
                        if id not in keyed:
                            cs.add("external_id", entity_id=person, scheme=id[0], value=id[1])
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{year}:{region}:dc{row['dc_person']}"),
                    person_id=person,
                    option_id=option,
                    role="list_member",
                    list_position=row["position"],
                    ballot_name=row["ballot_name"],
                    member_of_party_id=parties[key],
                    is_independent=False,
                )
                ranked += 1
            if listed:
                for note in stale.get(result_set, []):
                    cs.add(
                        "note_drop",
                        note_id=note,
                        entity_id=result_set,
                        body_md=UNRANKED,
                        reason="The full lists from the Statement of Persons Nominated are now loaded.",
                    )
                cs.add("citation", entity_id=result_set, source_id=sopns[listed[0]["sopn"]])
                cs.add("citation", entity_id=result_set, source_id=dc_source)
                if unlisted:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=UNLISTED)
                for note, body in grouped.get(result_set, []) if not unlisted else []:
                    if body == UNLISTED:
                        cs.add(
                            "note_drop",
                            note_id=note,
                            entity_id=result_set,
                            body_md=body,
                            reason="Every party's list is now recorded.",
                        )
            else:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNRANKED)
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
                            value(
                                pcon_set,
                                pcons[folded(name.replace("&", "and"))],
                                option_of(row["label"]),
                                VOTES,
                                row["value"],
                            )
                        else:
                            value(pcon_set, pcons[folded(name.replace("&", "and"))], None, VALID, row["value"])
        print(f"{rounds} members with a round, {len(persons)} persons, {ranked} unelected list members")
        return cs.write()
