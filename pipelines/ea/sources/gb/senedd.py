import html
import itertools
import re
import subprocess
import time
import unicodedata
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid5

import httpx
import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.gb.scottish_parliament import number, sheet
from ea.sources.gb import umit
from ea.store import DATA, connect

SOURCE = "gb.senedd"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
ELECTIONS = {
    1999: date(1999, 5, 6),
    2003: date(2003, 5, 1),
    2007: date(2007, 5, 3),
    2011: date(2011, 5, 5),
    2016: date(2016, 5, 5),
    2021: date(2021, 5, 6),
}
ERAS = {1999: 1999, 2003: 1999, 2007: 2007, 2011: 2007, 2016: 2007, 2021: 2007}
ERA_STARTS = {1999: ELECTIONS[1999], 2007: ELECTIONS[2007], 2026: date(2026, 5, 7)}
SENC_2026 = (
    "Afan Ogwr Rhondda",
    "Bangor Conwy Môn",
    "Blaenau Gwent Caerffili Rhymni",
    "Brycheiniog Tawe Nedd",
    "Caerdydd Ffynnon Taf",
    "Caerdydd Penarth",
    "Casnewydd Islwyn",
    "Ceredigion Penfro",
    "Clwyd",
    "Fflint Wrecsam",
    "Gwynedd Maldwyn",
    "Gŵyr Abertawe",
    "Pen-y-bont Bro Morgannwg",
    "Pontypridd Cynon Merthyr",
    "Sir Fynwy Torfaen",
    "Sir Gaerfyrddin",
)
TERMS = {
    "Q42310633": 1999,
    "Q42310642": 2003,
    "Q42310648": 2007,
    "Q42310652": 2011,
    "Q42310659": 2016,
    "Q106762850": 2021,
}
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
WAYBACK = "https://web.archive.org/web"
EC_2011 = f"{WAYBACK}/20250917201105if_/https://www.electoralcommission.org.uk/sites/default/files/excel_doc/NAW-2011-results-flat-file-WEB.xls"
EC_2016 = f"{WAYBACK}/20260208091026if_/https://www.electoralcommission.org.uk/sites/default/files/excel_doc/Electoral-Data-and-Results-National-Assembly-for-Wales-elections-May-2016.xls"
DEMON = f"{WAYBACK}/20170929073940if_/http://www.election.demon.co.uk/wales.html"
FIGSHARE = "https://ndownloader.figshare.com/files/42880132"
PDFS = {
    2007: ("senedd-research-2007", "https://research.senedd.wales/media/cnsagm43/07-063-english.pdf"),
    "2007b": ("senedd-2007-updated", "https://senedd.wales/media/nxnn2sz4/07-069-english.pdf"),
    2021: ("senedd-research-2021", "https://senedd.wales/media/gp0hueyu/21-008-senedd-election-2021-web.pdf"),
}
SPARQL = "https://query.wikidata.org/sparql"
MEMBER_QUERY = (
    "SELECT ?p ?pLabel ?pos ?start ?end ?dist ?distLabel ?party ?partyLabel WHERE { VALUES ?pos { "
    + " ".join(f"wd:{term}" for term in TERMS)
    + " } ?p p:P39 ?s . ?s ps:P39 ?pos . OPTIONAL{?s pq:P580 ?start} OPTIONAL{?s pq:P582 ?end} "
    "OPTIONAL{?s pq:P768 ?dist} OPTIONAL{?s pq:P4100 ?party} "
    'SERVICE wikibase:label { bd:serviceParam wikibase:language "en". } }'
)
MEMBERS_URL = f"{SPARQL}?{httpx.QueryParams({'query': MEMBER_QUERY})}"
SOURCES = (
    (
        "ec2011",
        "The Electoral Commission",
        "National Assembly for Wales election 2011: results flat file",
        "https://www.electoralcommission.org.uk/",
        "Open Government Licence v3.0",
        OGL,
        "Contains information from the Electoral Commission licensed under the Open Government Licence v3.0.",
        "official_body",
    ),
    (
        "ec2016",
        "The Electoral Commission",
        "National Assembly for Wales elections 2016: electoral data and results",
        "https://www.electoralcommission.org.uk/",
        "Open Government Licence v3.0",
        OGL,
        "Contains information from the Electoral Commission licensed under the Open Government Licence v3.0.",
        "official_body",
    ),
    (
        "senedd2007",
        "Senedd Research",
        "Assembly Election 2007: results and analysis",
        "https://research.senedd.wales/",
        "Parliamentary copyright",
        None,
        "Source: Members' Research Service, National Assembly for Wales.",
        "official_body",
    ),
    (
        "senedd2021",
        "Senedd Research",
        "Senedd Election 2021: Research Briefing",
        "https://research.senedd.wales/",
        "Senedd Commission Copyright, reproducible with acknowledgement",
        None,
        "Source: Senedd Research, © Senedd Commission copyright.",
        "official_body",
    ),
    (
        "demon",
        "Election Demon",
        "National Assembly for Wales election results 1999-2007",
        "http://www.election.demon.co.uk/wales.html",
        None,
        None,
        "Compiled from the returning officers' declarations by Election Demon (archived copy).",
        "aggregator",
    ),
    (
        "figshare",
        "Eoghan Kelly",
        "Welsh Election Data 1999-2021",
        "https://figshare.com/",
        "CC BY 4.0",
        "https://creativecommons.org/licenses/by/4.0/",
        "Welsh Election Data 1999-2021 by Eoghan Kelly, CC BY 4.0.",
        "academic",
    ),
    (
        "wikidata",
        "Wikidata",
        "Members of the National Assembly for Wales and the Senedd",
        "https://www.wikidata.org/",
        "CC0 1.0",
        "https://creativecommons.org/publicdomain/zero/1.0/",
        "Member names, constituencies, and parties from Wikidata (CC0).",
        "wikidata",
    ),
)
SOURCE_OF_YEAR = {1999: "demon", 2003: "demon", 2007: "senedd2007", 2011: "ec2011", 2016: "ec2016", 2021: "senedd2021"}
FIGSHARE_REGIONS = {
    "NW": "North Wales",
    "MWW": "Mid and West Wales",
    "SWC": "South Wales Central",
    "SWE": "South Wales East",
    "SWW": "South Wales West",
}
REGIONS = ("Mid and West Wales", "North Wales", "South Wales Central", "South Wales East", "South Wales West")
MAGNITUDE = 4

LABELS = {
    "con": ("Conservative", "conservative"),
    "conservative": ("Conservative", "conservative"),
    "c": ("Conservative", "conservative"),
    "lab": ("Labour", "labour"),
    "labour": ("Labour", "labour"),
    "lab/co-op": ("Labour", "labour"),
    "ld": ("Liberal Democrat", "liberal-democrats"),
    "lib": ("Liberal Democrat", "liberal-democrats"),
    "l dem": ("Liberal Democrat", "liberal-democrats"),
    "pc": ("Plaid Cymru", "plaid-cymru"),
    "ukip": ("UK Independence Party", "uk-independence-party"),
    "gp": ("Green Party", "green-party"),
    "grn": ("Green Party", "green-party"),
    "green": ("Green Party", "green-party"),
    "bnp": ("British National Party", "british-national-party"),
    "slp": ("Socialist Labour Party", "socialist-labour-party"),
    "s lab p": ("Socialist Labour Party", "socialist-labour-party"),
    "nlp": ("Natural Law Party", "natural-law-party"),
    "cpb": ("Communist Party of Britain", "communist-party-of-britain"),
    "com": ("Communist Party of Britain", "communist-party-of-britain"),
    "comm": ("Communist Party of Britain", "communist-party-of-britain"),
    "pla": ("Pro-Life Alliance", "pro-life-alliance"),
    "sa": ("Socialist Alternative", "socialist-alternative"),
    "v2stw": ("Vote 2 Stop the War", "vote-2-stop-the-war"),
    "ed": ("The English Democrats", "english-democrats"),
    "dem": ("Democratic Alliance for Wales", "democratic-alliance-for-wales"),
    "cpa": ("Christian People's Alliance", "christian-peoples-alliance"),
    "nmbp": ("New Millennium Bean Party", "new-millennium-bean-party"),
    "new millennium bean party": ("New Millennium Bean Party", "new-millennium-bean-party"),
    "nptrpa": ("Neath Port Talbot Rate Payers Association", "neath-port-talbot-rate-payers-association"),
    "pv": ("Blaenau Gwent People's Voice", "blaenau-gwent-people-s-voice"),
    "res": ("Respect", "respect"),
    "sep": ("Socialist Equality Party", "socialist-equality-party"),
    "ver": ("Veritas", "veritas"),
    "wcp": ("Welsh Christian Party", "welsh-christian-party"),
    "cpw": ("Welsh Christian Party", "welsh-christian-party"),
    "christian": ("Christian Party", "christian-party-proclaiming-christ-s-lordship"),
    "tusac": ("Trade Unionist and Socialist Coalition", "trade-unionist-and-socialist-coalition"),
    "tusc": ("Trade Unionist and Socialist Coalition", "trade-unionist-and-socialist-coalition"),
    "mrlp": ("The Official Monster Raving Loony Party", "official-monster-raving-loony-party"),
    "abolish": ("Abolish the Welsh Assembly Party", "abolish-the-welsh-assembly-party"),
    "abolish welsh assembly": ("Abolish the Welsh Assembly Party", "abolish-the-welsh-assembly-party"),
    "propel": ("Propel", "propel"),
    "gwlad": ("Gwlad", "gwlad"),
    "fa": ("Freedom Alliance", "freedom-alliance"),
    "reform": ("Reform UK", "reform-uk"),
    "nml": ("No More Lockdowns", "no-more-lockdowns"),
    "workers": ("Workers Party of Britain", "workers-party-of-britain"),
    "nf": ("National Front", "national-front"),
    "people first": ("People First", "people-first"),
    "womens equality party": ("Women's Equality Party", "women-s-equality-party"),
    "assoc of welsh inds": ("Association of Welsh Local Independents", "association-of-welsh-local-independents"),
    "freedom to choose": ("Freedom to Choose", "freedom-to-choose"),
    "monmouth's future": ("Monmouth's Future", "monmouths-future"),
}
INDEPENDENT = "Independent candidates"
NAMES = {
    "carmarthenwestandpembrokeshiresouth": "carmarthenwestandsouthpembrokeshire",
}
FIGSHARE_NAMES = {
    "bridgenda": "bridgend",
    "caernarfona": "caernarfon",
    "caerphillya": "caerphilly",
    "cardiffsoutha": "cardiffsouthandpenarth",
    "cardiffwesta": "cardiffwest",
    "clwydsoutha": "clwydsouth",
    "conwya": "conwy",
    "cynonvalleya": "cynonvalley",
    "islwyna": "islwyn",
    "meirionnydd nantconwya": "meirionnyddnantconwy",
    "meirionnyddnantconwya": "meirionnyddnantconwy",
    "ogmorea": "ogmore",
    "pontypridda": "pontypridd",
    "preseli": "preselipembrokeshire",
    "torfean": "torfaen",
}
SHARE_COLUMNS = {"conservative": "CON", "labour": "Lab", "liberal-democrats": "LD", "plaid-cymru": "PC"}
WIKIDATA_PARTIES = {
    "Welsh Labour": "labour",
    "Plaid Cymru": "plaid-cymru",
    "Welsh Conservative Party": "conservative",
    "Welsh Liberal Democrats": "liberal-democrats",
    "UK Independence Party": "uk-independence-party",
    "Reform UK": "reform-uk",
    "Blaenau Gwent People's Voice": "blaenau-gwent-people-s-voice",
}
AMS = (
    "The additional member system elects 60 members: 40 from single-member constituencies by first past the post and "
    "20 from five electoral regions, four for each region. Each voter has two votes: one for a constituency "
    "candidate and one for a party list or an individual candidate in the region. Each region's four list seats go "
    "to parties by the D'Hondt method, dividing a party's regional list votes by one more than the number of "
    "constituency seats it won in that region (plus the list seats it has already won), so the list compensates for "
    "disproportion in the constituency results. The declared seats are stored as published; the loader checked that the declared list seats reproduce by this "
    "method in every region."
)
PARTY_LEVEL = (
    "This result set is party-level: it carries each party's votes and the members elected, but not the individual "
    "candidates who stood and lost. {detail}"
)
DETAIL = {
    1999: "Votes are Election Demon's compilation of the returning officers' declarations, checked against the "
    "percentage shares in Eoghan Kelly's Welsh Election Data (CC BY 4.0). Parties the compilation prints only as "
    "abbreviations (P Rep, TFPW, U Soc) are combined with other candidates.",
    2003: "Votes are Election Demon's compilation of the returning officers' declarations, checked against the "
    "percentage shares in Eoghan Kelly's Welsh Election Data (CC BY 4.0); 13 constituency party figures differ "
    "from those shares by more than rounding and the compilation's are used. Parties the compilation prints only "
    "as abbreviations (CA, SP) are combined with other candidates.",
    2007: "Votes are from the Members' Research Service of the National Assembly for Wales; the regional ballot is "
    "reported by region.",
    2011: "Votes are from the Electoral Commission's results flat file, collected from returning officers; "
    "independent candidates are combined.",
    2016: "Votes are from the Electoral Commission's electoral data workbook, collected by the Elections Centre "
    "from returning officers.",
    2021: "Votes are from the Senedd Research briefing; the constituency ballot reports parties other than the "
    "named ones together, and the regional ballot is reported by region.",
}


def label_of(text: str) -> tuple[str, str | None]:
    cleaned = re.sub(r"\s+", " ", text.strip())
    found = LABELS.get(cleaned.lower())
    if found is not None:
        return found
    if re.fullmatch(r"(ind|independent)\b.*", cleaned, flags=re.IGNORECASE):
        return INDEPENDENT, None
    return cleaned, None


def fetch() -> None:
    for url, suffix in (
        (EC_2011, ".xls"),
        (EC_2016, ".xls"),
        (DEMON, ".html"),
        (FIGSHARE, ".xlsx"),
        *((url, ".pdf") for _, url in PDFS.values()),
    ):
        raw.fetch(SOURCE, url, suffix=suffix)
        time.sleep(1)
    try:
        raw.cached(SOURCE, MEMBERS_URL)
    except KeyError:
        response = httpx.get(
            SPARQL,
            params={"query": MEMBER_QUERY},
            headers={"Accept": "text/csv", "User-Agent": "electionsarchive/1.0 (https://electionsarchive.com)"},
            timeout=300,
        )
        response.raise_for_status()
        raw.store(
            SOURCE,
            MEMBERS_URL,
            response.content,
            suffix=".csv",
            retrieved_at=datetime.now().astimezone(),
            media_type="text/csv",
        )


def path_of(url: str) -> Path:
    return raw.cached(SOURCE, url).path


def pdf_lines(key: int | str) -> list[str]:
    text = subprocess.run(
        ["pdftotext", "-layout", str(path_of(PDFS[key][1])), "-"], capture_output=True, text=True, check=True
    ).stdout
    return text.splitlines()


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", html.unescape(text)).replace("&", " and ")
    key = re.sub(r"[^a-z]+", "", "".join(char for char in text.lower() if not unicodedata.combining(char)))
    return NAMES.get(key, key)


def pdf_lines(key: int | str) -> list[str]:
    return subprocess.run(
        ["pdftotext", "-layout", str(path_of(PDFS[key][1])), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()


def ints(tokens: list[str]) -> list[int | None]:
    return [None if token in (".", "-", "") else int(token.replace(",", "")) for token in tokens]


def pdf_table(lines: list[str], title: str, count: int, width: int, nth: int = 0) -> list[tuple[str, str, list[int | None]]]:
    starts = [index for index, line in enumerate(lines) if line.strip().startswith(title)]
    rows: list[tuple[str, str, list[int | None]]] = []
    region = ""
    for line in lines[starts[nth] + 1 :]:
        text = html.unescape(line.strip())
        if not text:
            continue
        tokens = text.split()
        tail = list(itertools.takewhile(lambda token: re.fullmatch(r"[\d,]+|\.|-", token), reversed(tokens)))
        if len(tail) >= width and len(tokens) > len(tail):
            rows.append(
                (region, " ".join(tokens[: len(tokens) - width]), ints(list(reversed(tokens))[:width][::-1]))
            )
            if len(rows) == count:
                break
            continue
        heading = text.replace("W est", "West")
        if heading in REGIONS:
            region = heading
    return rows


def parse_members() -> list[dict[str, object]]:
    frame = pl.read_csv(path_of(MEMBERS_URL).as_posix(), infer_schema_length=0)
    rows = []
    for row in frame.iter_rows(named=True):
        year = TERMS[row["pos"].rsplit("/", 1)[1]]
        if row["start"] is None or row["distLabel"] is None:
            continue
        rows.append(
            {
                "year": year,
                "qid": row["p"].rsplit("/", 1)[1],
                "name": row["pLabel"],
                "district": row["distLabel"],
                "start": datetime.fromisoformat(row["start"].replace("Z", "+00:00")).date(),
                "party": row["partyLabel"],
            }
        )
    return rows


def region_of(text: str) -> str:
    key = fold(text)
    return next(region for region in REGIONS if fold(region) == key)


def vote_rows(year: int, ballot: str, region: str, constituency: str | None, pairs: list[tuple[str, int | None]]) -> list[dict[str, object]]:
    totals: dict[tuple[str, str | None, str], int] = {}
    for label, votes in pairs:
        if votes is None:
            continue
        if ballot == "list" and re.match(r"(ind|independent)\s*[-(]", label, flags=re.IGNORECASE):
            inner = re.sub(r"^\w+\s*[-(]\s*|\)$", "", label.strip()).strip()
            name, key = LABELS.get(inner.lower(), (f"Independent ({inner})", None))
            identity = (name, key, "candidate" if key is None else "list")
        elif label in ("Oth", "Other", "Others"):
            identity = ("Other candidates", None, "others")
        else:
            name, key = label_of(label)
            if key is None and name != INDEPENDENT:
                name = "Other candidates"
            identity = (name, key, "others" if key is None else "list")
        totals[identity] = totals.get(identity, 0) + votes
    return [
        {"year": year, "ballot": ballot, "region": region, "constituency": constituency, "party": name, "key": key, "kind": kind, "votes": votes}
        for (name, key, kind), votes in totals.items()
    ]


def parse_demon(canon: dict[str, str]) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]], dict[tuple[int, str], str]]:
    text = path_of(DEMON).read_text(encoding="latin-1")
    start = text.index("BOROUGHS")
    split = text.index("REGIONAL LISTS")
    votes: list[dict[str, object]] = []
    units: list[dict[str, object]] = []
    declared: list[dict[str, object]] = []
    shares: dict[tuple[int, str], str] = {}
    heading = ""
    for chunk in re.split(r"<tr[^>]*>", text[start:]):
        found = re.search(r"<h2[^>]*>(.*?)</h2>", chunk)
        if found is not None:
            heading = html.unescape(re.sub(r"<[^>]+>", "", found.group(1))).strip()
            continue
        parts = re.split(r"<td[^>]*>", chunk)[1:]
        columns = [
            [html.unescape(re.sub(r"<[^>]+>", "", item)).strip() for item in re.split(r"<br\s*/?>", part.replace("</td>", ""))]
            for part in parts
        ]
        if not columns or columns[0][0] not in ("1999", "2003"):
            continue
        year = int(columns[0][0])
        electors = int(columns[1][0].replace(",", ""))
        if text.index(chunk) < split:
            name = re.sub(r"\s*\[\d+\]$", "", heading)
            county, _, rest = name.partition(", ")
            name = rest if county in ("CLWYD", "DYFED", "MID GLAMORGAN", "SOUTH GLAMORGAN", "WEST GLAMORGAN", "GWENT", "GWYNEDD", "POWYS") else name.replace(", ", " ")
            constituency = canon[fold(name)]
            parties, counts = columns[5], ints(columns[6])
            if len(counts) != len(parties) + 1:
                raise ValueError(f"{year} {constituency}: {len(parties)} parties, {len(counts)} counts")
            units.append({"year": year, "ballot": "constituency", "region": None, "constituency": constituency, "electorate": electors, "cast": None, "valid": None, "rejected": None})
            votes.extend(
                vote_rows(
                    year,
                    "constituency",
                    "",
                    constituency,
                    [(party, amount) for party, amount in zip(parties, counts[:-1], strict=True) if party != "" and amount is not None],
                )
            )
        else:
            region = region_of(heading)
            parties, counts = columns[3], ints(columns[4])
            seats = list(zip(ints(columns[8]), ints(columns[9]), strict=True))
            if not (len(parties) == len(counts) == len(seats)):
                raise ValueError(f"{year} {region}: ragged columns")
            units.append({"year": year, "ballot": "list", "region": region, "constituency": None, "electorate": electors, "cast": None, "valid": None, "rejected": None})
            votes.extend(vote_rows(year, "list", region, None, list(zip(parties, counts, strict=True))))
            for party, (constituency_seats, list_seats) in zip(parties, seats, strict=True):
                declared.append({"year": year, "region": region, "label": party, "constituency_seats": constituency_seats or 0, "list_seats": list_seats or 0})
    return votes, units, declared, shares


def parse_figshare() -> dict[tuple[int, str], dict[str, object]]:
    rows = sheet(path_of(FIGSHARE), "Data")
    header = rows[0]
    found: dict[tuple[int, str], dict[str, object]] = {}
    for row in rows[1:]:
        record = dict(zip(header, row, strict=False))
        election = record["Election"]
        if election is None or not election.endswith("c") or int(election[:4]) not in (1999, 2003):
            continue
        key = fold(record["conname"])
        key = FIGSHARE_NAMES.get(key, key)
        found[(int(election[:4]), key)] = {
            "region": FIGSHARE_REGIONS[record["Region"]],
            "shares": {key: float(record[key] or 0) for key in ("CON", "PC", "Lab", "LD")},
        }
    return found


def parse_ec2011(canon: dict[str, str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    votes: list[dict[str, object]] = []
    units: list[dict[str, object]] = []
    for ballot, title in (("constituency", "2011 Constituency Votes"), ("list", "2011 Regional Votes")):
        rows = sheet(path_of(EC_2011), title)
        header = rows[0]
        for row in rows[1:]:
            if row[1] is None or row[0] is None:
                continue
            record = dict(zip(header, row, strict=False))
            constituency = canon[fold(row[1])]
            region = region_of(row[0])
            pairs: list[tuple[str, int | None]] = []
            for position, column in enumerate(header):
                if position < 6 or column in ("Winner", "Others"):
                    continue
                amount = number(row[position])
                if column is None:
                    if amount:
                        raise ValueError(f"2011 {constituency}: unlabeled column with {amount}")
                    continue
                pairs.append((column, amount))
            pairs = [("Ind" if re.fullmatch(r"Ind[A-H]", label) else label, amount) for label, amount in pairs]
            valid = number(record["Valid votes"])
            if sum(amount or 0 for _, amount in pairs) != valid:
                raise ValueError(f"2011 {ballot} {constituency}: party columns do not sum to {valid}")
            if ballot == "constituency":
                votes.extend(vote_rows(2011, ballot, region, constituency, pairs))
            else:
                votes.extend(vote_rows(2011, ballot, region, constituency, [(label.replace("Ind-", "Ind (") + (")" if label.startswith("Ind-") else ""), amount) for label, amount in pairs]))
            units.append({"year": 2011, "ballot": ballot, "region": region, "constituency": constituency, "electorate": number(record["Electorate"]), "cast": None, "valid": valid, "rejected": number(record["Rejected ballots"])})
    return votes, units


def parse_ec2016(canon: dict[str, str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    votes: list[dict[str, object]] = []
    units: list[dict[str, object]] = []
    for ballot, title in (("constituency", "Constituency Results"), ("list", "Regional Results")):
        rows = sheet(path_of(EC_2016), title)
        header = rows[1]
        share = next(index for index, cell in enumerate(rows[0]) if cell == "VOTE SHARE %")
        first = next(index for index, cell in enumerate(rows[0]) if cell == "VOTES CAST")
        for row in rows[2:]:
            if row[0] is None or not row[0].startswith("W07"):
                continue
            record = dict(zip(header, row, strict=False))
            constituency = canon[fold(row[3])]
            region = region_of(row[1])
            pairs = [(header[index], number(row[index])) for index in range(first, share) if header[index] is not None]
            valid = number(record["Total valid votes cast"])
            if sum(amount or 0 for _, amount in pairs) != valid:
                raise ValueError(f"2016 {ballot} {constituency}: party columns do not sum to {valid}")
            pairs = [("Ind" if label == "IND" else label.strip(), amount) for label, amount in pairs]
            votes.extend(vote_rows(2016, ballot, region, constituency, pairs))
            rejected = record.get("Total number of ballot papers rejected at the count")
            units.append({"year": 2016, "ballot": ballot, "region": region, "constituency": constituency, "electorate": number(record["Electorate"]), "cast": number(record["Total ballots at the count"]), "valid": valid, "rejected": number(rejected)})
    return votes, units


def parse_2007(canon: dict[str, str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    lines = pdf_lines(2007)
    votes: list[dict[str, object]] = []
    units: list[dict[str, object]] = []
    const_columns = "Con Lab LD PC ED NMBP NPTRPA PV UKIP Ind1 Ind2 Ind3 Ind4 Ind5 Oth Total".split()
    list_columns = "Con Lab LD PC BNP CPA CPB ED GP Res SA SEP SLP UKIP Ver WCP Ind1 Ind2 Oth Total".split()
    electorate = {}
    section = False
    region = ""
    for line in pdf_lines("2007b"):
        text = line.strip()
        if text.startswith("3.5.") and "Electorate and Turnout" in text and not text.endswith(tuple("0123456789")):
            section = True
            continue
        if section and text.startswith("3.6."):
            break
        found = re.match(r"^(.+?)\s+([\d,]+)\s+[\d.]+%\s+[+-][\d.]+%$", text)
        if section and found is not None:
            electorate[fold(found.group(1))] = int(found.group(2).replace(",", ""))
    for region, name, counts in pdf_table(lines, "Constituency table - Votes, including minor parties", 40, 16, nth=-1):
        constituency = canon[fold(name)]
        pairs = [(column, amount) for column, amount in zip(const_columns, counts, strict=True) if column not in ("Oth", "Total")]
        pairs = [("Ind" if column.startswith("Ind") else column, amount) for column, amount in pairs]
        valid = counts[-1]
        if sum(amount or 0 for _, amount in pairs) != valid:
            raise ValueError(f"2007 {constituency}: party columns do not sum to {valid}")
        votes.extend(vote_rows(2007, "constituency", region, constituency, pairs))
        units.append({"year": 2007, "ballot": "constituency", "region": region, "constituency": constituency, "electorate": electorate[fold(name)], "cast": None, "valid": valid, "rejected": None})
    for _, name, counts in pdf_table(lines, "Regional table - Votes, including minor parties", 5, 20, nth=-1):
        region = region_of(name)
        pairs = [(column, amount) for column, amount in zip(list_columns, counts, strict=True) if column not in ("Oth", "Total")]
        pairs = [("Ind" if column.startswith("Ind") else column, amount) for column, amount in pairs]
        valid = counts[-1]
        if sum(amount or 0 for _, amount in pairs) != valid:
            raise ValueError(f"2007 {region}: regional party columns do not sum to {valid}")
        votes.extend(vote_rows(2007, "list", region, None, pairs))
        units.append({"year": 2007, "ballot": "list", "region": region, "constituency": None, "electorate": None, "cast": None, "valid": valid, "rejected": None})
    return votes, units


def parse_2021(canon: dict[str, str]) -> tuple[list[dict[str, object]], list[dict[str, object]]]:
    lines = pdf_lines(2021)
    votes: list[dict[str, object]] = []
    units: list[dict[str, object]] = []
    columns = "Con Lab LD PC UKIP Abolish GP Oth Total Majority".split()
    table = pdf_table(lines, "Table 4: Constituency Results", 40, 10, nth=-1)
    begin = [index for index, line in enumerate(lines) if line.strip().startswith("Table 5: Constituency results")][-1]
    electorates = []
    for line in lines[begin + 1 :]:
        if line.strip().startswith("Table 6"):
            break
        tokens = line.split()
        if sum(token.endswith("%") for token in tokens) >= 8:
            electorates.append(int(tokens[-2].replace(",", "")))
    for (region, name, counts), electorate in zip(table, electorates, strict=True):
        constituency = canon[fold(name)]
        pairs = [(column, amount) for column, amount in zip(columns, counts, strict=True) if column not in ("Total", "Majority")]
        valid = counts[8]
        if sum(amount or 0 for _, amount in pairs) != valid:
            raise ValueError(f"2021 {constituency}: party columns do not sum to {valid}")
        votes.extend(vote_rows(2021, "constituency", region, constituency, pairs))
        units.append({"year": 2021, "ballot": "constituency", "region": region, "constituency": constituency, "electorate": electorate, "cast": None, "valid": valid, "rejected": None})
    begin = [index for index, line in enumerate(lines) if line.strip().startswith("Table 11: Regional table")][-1]
    by_region: dict[str, list[tuple[str, int | None]]] = {region: [] for region in REGIONS}
    totals: dict[str, int | None] = {}
    for line in lines[begin + 1 :]:
        found = re.match(r"^\s*([A-Za-z]+)\s+((?:[\d,]+\s+){5}[\d,]+)\s*$", line)
        if found is None:
            continue
        label, cells = found.group(1), ints(found.group(2).split())
        if label == "Other":
            continue
        for region, amount in zip(REGIONS, cells[:5], strict=True):
            if label == "Total":
                totals[region] = amount
            else:
                by_region[region].append((label, amount))
        if label == "Total":
            break
    for region in REGIONS:
        pairs = by_region[region]
        if sum(amount or 0 for _, amount in pairs) != totals[region]:
            raise ValueError(f"2021 {region}: regional party columns do not sum to {totals[region]}")
        votes.extend(vote_rows(2021, "list", region, None, pairs))
        units.append({"year": 2021, "ballot": "list", "region": region, "constituency": None, "electorate": None, "cast": None, "valid": totals[region], "rejected": None})
    return votes, units


def dhondt(votes: dict[str, int], held: dict[str, int], seats: int) -> dict[str, int]:
    won = dict.fromkeys(votes, 0)
    for _ in range(seats):
        winner = max(votes, key=lambda key: votes[key] / (1 + held.get(key, 0) + won[key]))
        won[winner] += 1
    return {key: count for key, count in won.items() if count}


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    members = parse_members()
    canon = {fold(row["district"]): row["district"] for row in members if row["district"] not in REGIONS}
    demon_votes, demon_units, declared, _ = parse_demon(canon)
    figshare = parse_figshare()
    totals: dict[tuple[int, str], int] = {}
    for row in demon_votes:
        if row["ballot"] == "constituency":
            row["region"] = figshare[(row["year"], fold(row["constituency"]))]["region"]
            key = (row["year"], row["constituency"])
            totals[key] = totals.get(key, 0) + row["votes"]
    for row in demon_units:
        if row["ballot"] == "constituency":
            row["region"] = figshare[(row["year"], fold(row["constituency"]))]["region"]
    checked = 0
    for row in demon_votes:
        if row["ballot"] != "constituency" or row["key"] not in SHARE_COLUMNS:
            continue
        share = figshare[(row["year"], fold(row["constituency"]))]["shares"][SHARE_COLUMNS[row["key"]]]
        if abs(row["votes"] / totals[(row["year"], row["constituency"])] - share) > 0.0016:
            print(f"SHARE {row['year']} {row['constituency']} {row['party']}: {row['votes']} votes is {row['votes'] / totals[(row['year'], row['constituency'])]:.4f}, Figshare {share}")
        checked += 1
    print("Election Demon shares checked against Figshare:", checked)
    ec11_votes, ec11_units = parse_ec2011(canon)
    ec16_votes, ec16_units = parse_ec2016(canon)
    s07_votes, s07_units = parse_2007(canon)
    s21_votes, s21_units = parse_2021(canon)
    votes = pl.DataFrame([*demon_votes, *s07_votes, *ec11_votes, *ec16_votes, *s21_votes], infer_schema_length=None)
    units = pl.DataFrame([*demon_units, *s07_units, *ec11_units, *ec16_units, *s21_units], infer_schema_length=None)
    pl.DataFrame(declared).write_parquet(PARSED / "declared.parquet")
    elected = []
    problems = []
    for year, day in ELECTIONS.items():
        pool = [row for row in members if row["year"] == year and day <= row["start"] <= day + timedelta(days=100)]
        held = votes.filter((pl.col("year") == year) & (pl.col("ballot") == "constituency"))
        seats: dict[str, dict[str, int]] = {region: {} for region in REGIONS}
        for constituency in sorted(held["constituency"].unique()):
            rows = held.filter(pl.col("constituency") == constituency).sort("votes", descending=True)
            top = rows.row(0, named=True)
            found = sorted((row for row in pool if row["district"] == constituency), key=lambda row: row["start"])
            if not found:
                problems.append(f"{year} {constituency}: no member in Wikidata")
                continue
            member = found[0]
            keys = {WIKIDATA_PARTIES.get(row["party"]) for row in found if row["start"] == member["start"] and row["qid"] == member["qid"]}
            if top["key"] not in keys:
                problems.append(f"{year} {constituency}: top {top['party']} ({top['key']}) vs member {member['name']} {keys}")
            seats[top["region"]][top["key"] or "independent"] = seats[top["region"]].get(top["key"] or "independent", 0) + 1
            elected.append({"year": year, "ballot": "constituency", "district": constituency, "region": top["region"], "qid": member["qid"], "name": member["name"], "party": top["party"], "key": top["key"]})
        listed = votes.filter((pl.col("year") == year) & (pl.col("ballot") == "list") & (pl.col("kind") == "list"))
        for region in REGIONS:
            found = sorted((row for row in pool if row["district"] == region), key=lambda row: row["start"])
            chosen = {}
            for row in found:
                chosen.setdefault(row["qid"], []).append(row)
            members_here = list(chosen.values())[:MAGNITUDE]
            if len(chosen) != MAGNITUDE:
                problems.append(f"{year} {region}: {len(chosen)} list members in Wikidata")
            region_votes = listed.filter(pl.col("region") == region)
            by_key = dict(region_votes.group_by("key").agg(pl.col("votes").sum()).iter_rows())
            expected = dhondt(by_key, seats[region], MAGNITUDE)
            actual: dict[str, int] = {}
            for rows in members_here:
                options = {WIKIDATA_PARTIES.get(row["party"]) for row in rows} & set(by_key)
                key = next(iter(options)) if len(options) == 1 else None
                if len(options) != 1:
                    problems.append(f"{year} {region}: {rows[0]['name']} party {[row['party'] for row in rows]} not a list option")
                actual[key or "?"] = actual.get(key or "?", 0) + 1
                label = region_votes.filter(pl.col("key") == key)["party"].to_list()
                elected.append({"year": year, "ballot": "list", "district": region, "region": region, "qid": rows[0]["qid"], "name": rows[0]["name"], "party": label[0] if label else None, "key": key})
            if expected != actual:
                problems.append(f"{year} {region}: D'Hondt gives {expected}, members are {actual}")
    pl.DataFrame(elected, infer_schema_length=None).write_parquet(PARSED / "members.parquet")
    votes.write_parquet(PARSED / "votes.parquet")
    units.write_parquet(PARSED / "units.parquet")
    for line in problems:
        print("PROBLEM", line)
    print(len(votes), "vote rows,", len(units), "units,", len(elected), "members,", len(problems), "problems")


def emit() -> Path:
    votes = pl.read_parquet(PARSED / "votes.parquet")
    units = pl.read_parquet(PARSED / "units.parquet")
    members = pl.read_parquet(PARSED / "members.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="National Assembly for Wales and Senedd elections 1999–2021 by party, from the Electoral Commission, Senedd Research, Election Demon, and Wikidata",
            polity="GB-WLS",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        wales = cs.polity("GB-WLS")
        gb = cs.polity("GB")
        country = cs.area("gb.country", "WLS")
        if country in cs.minted:
            raise ValueError("publish gb.umit first: the countries must exist")
        for code, rank, noun in (("gb.senr", 1, "electoral region"), ("gb.senc", 2, "constituency")):
            if conn.execute("select 1 from scheme where code = %s", [code]).fetchone() is None:
                cs.add("scheme", code=code, polity_id=wales, kind="electoral_district", rank=rank, noun=noun)
        fptp = cs.key("gb.electoral_system", "fptp")
        if fptp in cs.minted:
            raise ValueError("publish gb.parliament first: first past the post must exist")
        ams = cs.key("gb.electoral_system", "welsh-ams-list")
        cs.add(
            "electoral_system",
            id=ams,
            polity_id=wales,
            rules={"formula": "dhondt", "compensation": "constituency_seats"},
            rules_version=1,
        )
        cs.add("note", entity_id=ams, kind="methodology", body_md=AMS)
        if ams in cs.minted:
            name(ams, "Additional member system, regional lists (Senedd Cymru)")
        body = cs.key("gb.body", "senedd")
        if body in cs.minted:
            cs.add("body", id=body, polity_id=wales, kind="unicameral")
            name(body, "Senedd Cymru")

        sources: dict[str, UUID] = {}
        for key, publisher, title, url, licence, licence_url, attribution, kind in SOURCES:
            sources[key] = cs.source(publisher, title)
            cs.add(
                "source",
                id=sources[key],
                kind=kind,
                publisher=publisher,
                title=title,
                url=url,
                license=licence,
                license_url=licence_url,
                attribution=attribution,
            )
        for url, key in (
            (EC_2011, "ec2011"),
            (EC_2016, "ec2016"),
            (DEMON, "demon"),
            (FIGSHARE, "figshare"),
            (PDFS[2007][1], "senedd2007"),
            (PDFS["2007b"][1], "senedd2007"),
            (PDFS[2021][1], "senedd2021"),
            (MEMBERS_URL, "wikidata"),
        ):
            cs.artifact(raw.cached(SOURCE, url), sources[key])

        parties: dict[str, UUID] = {}

        def party_of(key: str | None, label: str | None) -> UUID | None:
            if key is None:
                return None
            if key not in parties:
                if key in umit.EXISTING:
                    parties[key] = cs.key(*umit.EXISTING[key])
                else:
                    parties[key] = cs.key("gb.party", key)
                    if parties[key] in cs.minted:
                        cs.add("party", id=parties[key], kind="party", polity_id=gb)
                        name(parties[key], umit.NAMES.get(key, label or key))
            return parties[key]

        christian = party_of("christian-party-proclaiming-christ-s-lordship", None)
        stray = conn.execute("select entity_id from external_id where scheme = 'gb.party' and value = 'christian-party'").fetchone()
        if stray is not None and stray[0] != christian:
            cs.merge(stray[0], christian)
        official = 'Christian Party "Proclaiming Christ\'s Lordship"'
        for name_id, text in conn.execute(
            "select id, text from name where entity_id = %s and kind = 'official' and is_preferred and text <> %s",
            (christian, official),
        ).fetchall():
            cs.add("name_drop", name_id=name_id, entity_id=christian, text=text, reason="The Electoral Commission register gives the party's name with its description in quotation marks.")
        name(christian, official)

        starts = sorted(ERA_STARTS)
        ends = dict(itertools.pairwise(starts))
        region_areas: dict[tuple[int, str], UUID] = {}
        region_units: dict[tuple[int, str], UUID] = {}
        constituency_areas: dict[tuple[int, str], UUID] = {}
        constituency_units: dict[tuple[int, str], UUID] = {}
        for era in starts:
            start = ERA_STARTS[era]
            end = ERA_STARTS[ends[era]] if era in ends else None
            years = [year for year, value in ERAS.items() if value == era]
            located = {
                row["constituency"]: row["region"]
                for row in votes.filter((pl.col("year").is_in(years)) & (pl.col("ballot") == "constituency"))
                .select("constituency", "region")
                .unique()
                .iter_rows(named=True)
            }
            if len(located) != votes.filter(pl.col("year").is_in(years) & (pl.col("ballot") == "constituency"))["constituency"].n_unique():
                raise ValueError(f"{era}: a constituency changes region within one boundary era")
            if era == 2026:
                places = [("gb.senc", label, country, constituency_areas, constituency_units, None) for label in SENC_2026]
            else:
                places = [("gb.senr", label, country, region_areas, region_units, label) for label in REGIONS] + [
                    ("gb.senc", label, None, constituency_areas, constituency_units, located[label]) for label in sorted(located)
                ]
            for scheme, label, parent, areas, area_units, region in places:
                code = f"{era}:{slug(label)}"
                parent_id = parent if parent is not None else region_areas[(era, region)]
                area = areas[(era, label)] = cs.area(scheme, code, start)
                unit = area_units[(era, label)] = cs.unit(scheme, code)
                if area in cs.minted:
                    cs.add("area", id=area, scheme_code=scheme)
                    name(area, label)
                cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
                cs.add("area_parent", area_id=area, parent_id=parent_id, valid_from=start, valid_to=end)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=wales,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=start,
                    valid_to=end,
                )
                if unit in cs.minted:
                    name(unit, label)

        persons: dict[str, UUID] = {}

        def person_of(qid: str, text: str) -> UUID:
            if qid not in persons:
                persons[qid] = cs.key("wikidata", qid)
                if persons[qid] in cs.minted:
                    cs.add("person", id=persons[qid])
                    name(persons[qid], text)
            return persons[qid]

        def value(result_set: UUID, unit: UUID, option: UUID | None, measure: int, amount: int | None) -> None:
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

        def figures(result_set: UUID, unit: UUID, row: dict[str, object]) -> None:
            for measure, field in ((REGISTERED, "electorate"), (BALLOTS, "cast"), (VALID, "valid"), (INVALID, "rejected")):
                value(result_set, unit, None, measure, row[field])

        for year, day in ELECTIONS.items():
            era = ERAS[year]
            noun = "National Assembly for Wales" if year < 2021 else "Senedd"
            election = cs.key("gb.election", f"{day}:senedd")
            cs.add("election", id=election, polity_id=wales, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} {noun} election")
            path = f"gb/wales/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            source_id = sources[SOURCE_OF_YEAR[year]]
            caveat = PARTY_LEVEL.format(detail=DETAIL[year])
            year_votes = votes.filter(pl.col("year") == year)
            year_units = units.filter(pl.col("year") == year)
            winners = members.filter((pl.col("year") == year) & (pl.col("ballot") == "constituency"))
            seats_by_region: dict[str, dict[str, int]] = {}

            for (constituency,), rows in year_votes.filter(pl.col("ballot") == "constituency").group_by("constituency", maintain_order=True):
                contest_key = f"{day}:{slug(constituency)}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=constituency_areas[(era, constituency)],
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {noun} election in {constituency}")
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
                    source_id=source_id,
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="caveat", body_md=caveat)
                unit = constituency_units[(era, constituency)]
                figures(result_set, unit, year_units.filter((pl.col("ballot") == "constituency") & (pl.col("constituency") == constituency)).row(0, named=True))
                top = rows.sort("votes", descending=True).row(0, named=True)
                member = winners.filter(pl.col("district") == constituency).row(0, named=True)
                for line in rows.iter_rows(named=True):
                    option = cs.key("gb.option", f"{contest_key}:{slug(line['party'])}")
                    party = party_of(line["key"], line["party"])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if line["kind"] == "others" else "candidate",
                        party_id=party,
                        ballot_label=line["party"],
                    )
                    value(result_set, unit, option, VOTES, line["votes"])
                    if line["party"] == top["party"]:
                        cs.add(
                            "candidacy",
                            id=cs.key("gb.candidacy", f"{contest_key}:{member['qid']}"),
                            person_id=person_of(member["qid"], member["name"]),
                            option_id=option,
                            role="head",
                            ballot_name=member["name"],
                            member_of_party_id=party,
                            is_independent=party is None,
                        )
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")

            list_by_constituency = year in (2011, 2016)
            for region in REGIONS:
                label = region
                contest_key = f"{day}:list:{slug(label)}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=region_areas[(era, region)],
                    magnitude=MAGNITUDE,
                    round_number=1,
                    electoral_system_id=ams,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {noun} election, {label} regional list")
                cs.add("slug", path=f"{path}/{slug(label)}-regional-list", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source_id,
                    is_default=True,
                    completeness=1,
                )
                cs.add("note", entity_id=result_set, kind="caveat", body_md=caveat)
                region_votes = year_votes.filter((pl.col("ballot") == "list") & (pl.col("region") == region))
                options: dict[str, UUID] = {}
                by_party: dict[str, UUID] = {}
                for line in region_votes.select("party", "key", "kind").unique(maintain_order=True).iter_rows(named=True):
                    option = options[line["party"]] = cs.key("gb.option", f"{contest_key}:{slug(line['party'])}")
                    party = party_of(line["key"], line["party"])
                    if line["key"] is not None:
                        by_party[line["key"]] = option
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind=line["kind"],
                        party_id=party,
                        ballot_label=line["party"],
                    )
                elected: dict[str, list[dict[str, object]]] = {}
                for line in members.filter((pl.col("year") == year) & (pl.col("ballot") == "list") & (pl.col("region") == region)).iter_rows(named=True):
                    elected.setdefault(line["key"], []).append(line)
                for key, winners_here in elected.items():
                    option = by_party[key]
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="won", seats=len(winners_here))
                    for line in winners_here:
                        candidacy = cs.key("gb.candidacy", f"{contest_key}:{line['qid']}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person_of(line["qid"], line["name"]),
                            option_id=option,
                            role="list_member",
                            ballot_name=line["name"],
                            member_of_party_id=party_of(key, line["party"]),
                            is_independent=False,
                        )
                        cs.add("outcome", result_set_id=result_set, option_id=option, candidacy_id=candidacy, kind="elected")
                if list_by_constituency:
                    for row in year_units.filter((pl.col("ballot") == "list") & (pl.col("region") == region)).iter_rows(named=True):
                        unit = constituency_units[(era, row["constituency"])]
                        figures(result_set, unit, row)
                        for line in region_votes.filter(pl.col("constituency") == row["constituency"]).iter_rows(named=True):
                            value(result_set, unit, options[line["party"]], VOTES, line["votes"])
                else:
                    unit = region_units[(era, region)]
                    figures(result_set, unit, year_units.filter((pl.col("ballot") == "list") & (pl.col("region") == region)).row(0, named=True))
                    for line in region_votes.iter_rows(named=True):
                        value(result_set, unit, options[line["party"]], VOTES, line["votes"])
        for before, after in itertools.pairwise(starts):
            if after == 2026:
                event = uuid5(NAMESPACE, f"area_event\x1fgb.senr\x1f{after}")
                cs.add(
                    "area_event",
                    id=event,
                    kind="boundary_changed",
                    date=ERA_STARTS[after],
                    notes="The five electoral regions were replaced by 16 constituencies as the areas that elect the "
                    "Senedd's members, each choosing six from closed party lists, for the 2026 election.",
                )
                for (era, _), area in region_areas.items():
                    if era == before:
                        cs.add("area_event_member", event_id=event, area_id=area, role="from")
                for (era, _), area in constituency_areas.items():
                    if era == after:
                        cs.add("area_event_member", event_id=event, area_id=area, role="to")
                continue
            for (era, label), area in region_areas.items():
                if era != after:
                    continue
                event = uuid5(NAMESPACE, f"area_event\x1fgb.senr\x1f{after}\x1f{slug(label)}")
                cs.add(
                    "area_event",
                    id=event,
                    kind="boundary_changed",
                    date=ERA_STARTS[after],
                    notes=f"The {label} electoral region was redefined for the {after} election.",
                )
                cs.add("area_event_member", event_id=event, area_id=region_areas[(before, label)], role="from")
                cs.add("area_event_member", event_id=event, area_id=area, role="to")
        return cs.write()
