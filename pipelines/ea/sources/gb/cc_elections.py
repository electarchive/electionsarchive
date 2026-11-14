import html
import re
import subprocess
import sys
import time
import zipfile
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.names import display
from ea.sources.gb.parish import slug
from ea.store import DATA, connect

SOURCE = "gb.cc_elections"
VERSION = "1"
SCHEME = "gb.community_council"
PARSED = DATA / "parsed" / SOURCE
SOURCES = Path(__file__).parent / "cc_elections_sources.tsv"
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
HIGHLAND = "S12000017"
READ = {
    HIGHLAND,
    "S12000048",
    "S12000030",
    "S12000027",
    "S12000035",
    "S12000005",
    "S12000010",
    "S12000020",
    "S12000041",
    "S12000029",
    "S12000006",
    "S12000034",
    "S12000026",
}
TRANSCRIBED = Path(__file__).parent / "cc_elections_transcribed.tsv"
INBOX = DATA / "inbox/manual/MF-668"
DELIVERED = {
    "https://www.pkc.gov.uk/media/54467/Pitlochry-CC-WEB-VERSION/pdf/Pitlochry_CC_-_WEB_VERSION.pdf": "Pitlochry_CC_-_WEB_VERSION.pdf",
    "https://www.pkc.gov.uk/media/55580": "WEB_VERSION_Meigle___Ardler_CC_-_Duly_Elected.pdf",
    "https://www.stirling.gov.uk/media/dj5bpik0/community-council-election-results-2023.pdf": "community-council-election-results-2023.pdf",
    "https://www.shetland.gov.uk/downloads/file/11011/notice-of-uncontested-elections-2025": "Notice_of_Uncontest_Elections.pdf",
}
COUNCILS = {
    "S12000017": (
        "The Highland Council",
        "Highland Council Scheme of Establishment for Community Councils",
        "https://www.highland.gov.uk/downloads/file/3749/community-council-scheme-of-establishment-2025",
        (
            "Community councils in Highland are formed under the Highland Council's Scheme of Establishment for "
            "Community Councils, made under Part IV of the Local Government (Scotland) Act 1973. The council's "
            "returning officer runs the elections: where nominations do not exceed the places, the nominees are "
            "declared elected without a poll; where they exceed them, a postal ballot is held in which each elector "
            "may vote for as many candidates as there are places, and the candidates with the most votes are elected. "
            "A community council is not formed when nominations fall short of the scheme's minimum."
        ),
    ),
    "S12000035": (
        "Argyll and Bute Council",
        "Argyll and Bute Council Scheme of Establishment for Community Councils 2026",
        "https://www.argyll-bute.gov.uk/sites/default/files/2026-08/Approved%20CC%20Scheme%20of%20Establishment%202026.pdf",
        (
            "Community councils in Argyll and Bute are formed under the council's Scheme of Establishment for Community Councils, made under the Local Government (Scotland) Act 1973. Where the candidates validly nominated do not exceed the places, they are declared elected; where they exceed them, an election is held by postal ballot and each elector may vote for candidates up to the number of places."
        ),
    ),
    "S12000023": (
        "Orkney Islands Council",
        "Orkney Islands Council Scheme for Community Councils",
        "https://www.orkney.gov.uk/media/okmj4oz5/scheme-for-community-councils-2022.pdf",
        (
            "Community councils in Orkney are formed under the Orkney Islands Council's Scheme for Community Councils, made under the Local Government (Scotland) Act 1973, and the council's returning officer declares each result. This record is built from the declaration of result, not from the scheme text, which was not read."
        ),
    ),
    "S12000005": (
        "Clackmannanshire Council",
        "Clackmannanshire Council Scheme for the Establishment of Community Councils",
        "https://www.clacks.gov.uk/document/5811.pdf",
        (
            "Community councils in Clackmannanshire are formed under the council's scheme, made under the Local Government (Scotland) Act 1973. Where nominations do not exceed the places to be filled, the nominees are declared elected unopposed; where they exceed them, a ballot is held and community councils are elected on the block voting system."
        ),
    ),
    "S12000029": (
        "South Lanarkshire Council",
        "South Lanarkshire Council Scheme for Establishment of Community Councils",
        "https://www.southlanarkshire.gov.uk/downloads/file/9067/scheme-for-establishment-of-community-councils",
        (
            "Community councils in South Lanarkshire are formed under the council's scheme for establishment of community councils, made under the Local Government (Scotland) Act 1973. Where the candidates validly nominated exceed the maximum membership for the area, an election is arranged."
        ),
    ),
    "S12000010": (
        "East Lothian Council",
        "East Lothian Council community council elections",
        "https://www.eastlothian.gov.uk/safety-and-community/your-community/get-involved-and-have-your-say/community-council-election",
        (
            "Community councils in East Lothian are formed under the council's Scheme of Establishment of Community Councils, made under the Local Government (Scotland) Act 1973, and the council's returning officer runs the elections. Where nominations do not exceed the places, the nominees are declared elected without a contest; where they exceed them, a ballot is held. This record is built from the council's notices, not from the scheme text, which the council's site no longer serves."
        ),
    ),
    "S12000020": (
        "The Moray Council",
        "Moray Council Scheme for the Establishment of Community Councils 2025-2029",
        "https://www.moray.gov.uk/media/cavjgsgg/file137736.pdf",
        (
            "Community councils in Moray are formed under the Moray Council's scheme for the establishment of community councils, made under the Local Government (Scotland) Act 1973, and the council runs the elections."
        ),
    ),
    "S12000041": (
        "Angus Council",
        "Angus Council Scheme for the Establishment of Community Councils 2022",
        "https://www.angus.gov.uk/sites/default/files/2022-06/Report%20176%20Review%20of%20Scheme%20of%20Community%20Councils_App1.pdf",
        (
            "Community councils in Angus are formed under the council's Scheme for the Establishment of Community Councils, made under the Local Government (Scotland) Act 1973. Where the candidates validly nominated number at least half of the maximum membership but no more than the maximum, they are declared elected and no ballot is held; where they exceed the maximum, a poll is held and each elector may vote for candidates up to the number of vacancies, no more than once for each; below half, no community council is formed."
        ),
    ),
    "S12000013": (
        "Comhairle nan Eilean Siar",
        "Comhairle nan Eilean Siar Community Council Revised Scheme of Establishment 2023",
        "https://www.cne-siar.gov.uk/sites/default/files/2025-04/Community-Council-Revised-Scheme-of-Establishment-2023.pdf",
        (
            "Community councils in the Western Isles are formed under the Comhairle's Revised Scheme of Establishment, made under the Local Government (Scotland) Act 1973. Where the candidates validly nominated exceed the maximum number of places, a poll is held by post and each voter may vote for up to the number of places."
        ),
    ),
    "S12000048": (
        "Perth and Kinross Council",
        "Perth and Kinross Council community council elections and by-elections",
        "https://www.pkc.gov.uk/article/14908/Community-Council-elections-and-by-elections",
        (
            "Community councils in Perth and Kinross are formed under the council's Scheme of Establishment of "
            "Community Councils, made under the Local Government (Scotland) Act 1973, and the council's returning "
            "officer for community councils declares each result. This record is built from the council's notices "
            "that the undernoted members are duly declared elected, not from the scheme text."
        ),
    ),
    "S12000030": (
        "Stirling Council",
        "Stirling Council Scheme of Establishment for Community Councils",
        "https://www.stirling.gov.uk/community-life-and-leisure/your-community/about-community-councils/scheme-of-establishment",
        (
            "Community councils in Stirling are formed under the council's Scheme of Establishment, made under the "
            "Local Government (Scotland) Act 1973. This record is built from the council's list of the community "
            "councils established in 2023 without a ballot because enough valid nominations were received, not from "
            "the scheme text. The list gives members only, not the places to be filled."
        ),
    ),
    "S12000006": (
        "Dumfries and Galloway Council",
        "Amended Scheme for the Establishment of Community Councils in Dumfries and Galloway",
        "https://www.dumgal.gov.uk/media/20726/Scheme-for-the-Establishment-of-Community-Councils/pdf/Sceheme_for_the_Establishment_of_Community_Councils_October_2020.pdf",
        (
            "Community councils in Dumfries and Galloway are formed under the council's Scheme for the Establishment of Community Councils, made under the Local Government (Scotland) Act 1973, and the council's returning officer runs the elections. Where the candidates validly nominated reach the minimum but do not exceed the maximum membership in Schedule 1 of the scheme, they are declared elected without a ballot; where they exceed it, a poll is held at a polling place and each voter may vote for candidates up to the number of vacancies, no more than once for each; below the minimum, no community council is established."
        ),
    ),
    "S12000034": (
        "Aberdeenshire Council",
        "Aberdeenshire Council community council elections",
        "https://www.aberdeenshire.gov.uk/communities-and-events/community-councils/cc-election",
        (
            "Community councils in Aberdeenshire are formed under the council's scheme for the establishment of community councils, made under the Local Government (Scotland) Act 1973. In 2024 all of them sought members at the same time for the first time, with the next election due in three years. Where nominations exceed the places, a ballot is held; where they fall short of a council's minimum, no community council is formed. This record is built from the council's area pages, not from the scheme text."
        ),
    ),
    "S12000026": (
        "Scottish Borders Council",
        "Ballot rescheduled for Coldingham Community Council election",
        "https://www.scotborders.gov.uk/news/article/4526/ballot-rescheduled-for-coldingham-community-council-election",
        (
            "Community councils in the Scottish Borders are formed under the council's scheme for community councils, made under the Local Government (Scotland) Act 1973, and the council runs their elections. At the Coldingham ballot, each voter was entitled to 10 votes and the 10 seats were filled by the 10 candidates with the most votes. This record is built from the council's news releases, not from the scheme text."
        ),
    ),
    "S12000027": (
        "Shetland Islands Council",
        "Shetland Islands Council community council elections 2025",
        "https://www.shetland.gov.uk/elections-voting/community-council-elections-2025",
        (
            "Community councils in Shetland are formed under the council's Scheme of Establishment for Community "
            "Councils, made under the Local Government (Scotland) Act 1973, and the council's returning officer "
            "publishes the result. This record is built from the notice of uncontested community council elections "
            "of 2025, which lists the candidates declared elected, the places available, and the vacancies left to "
            "be filled by co-option."
        ),
    ),
}
ALIASES = {
    ("S12000006", "Buittle"): "Buittle Parish",
    ("S12000006", "Templand and District"): "Templand",
    ("S12000006", "Port William and District"): "Port William",
    ("S12000006", "Royal Burgh of Wigtown"): "Royal Burgh of Wigtown and District",
    ("S12000006", "The Royal Burgh of Lochmaben and District"): "Royal Burgh of Lochmaben and District",
    ("S12000006", "The Royal Burgh of Kirkcudbright and District"): "Royal Burgh of Kirkcudbright and District",
    ("S12000006", "The Royal Burgh of New Galloway and Kells"): "The Royal Burgh of New Galloway and Kells Parish",
    ("S12000006", "The Royal Burgh of Sanquhar and District"): "Royal Burgh of Sanquhar and District",
    ("S12000006", "St Johns Town of Dalry"): "Dalry",
    ("S12000006", "Stranraer Ochtrelure and Belmont"): "Stranraer, Ochtrelure and Belmont",
    ("S12000034", "Boddam"): "Boddam and District",
    ("S12000034", "Fraserburgh and District"): "Fraserburgh",
    ("S12000034", "Fordyce, Sandend and Rural District"): "Fordyce, Sandend and District",
    ("S12000034", "Fyvie, Rothienorman and Monquhitter"): "Fyvie Rothienorman Monquhitter",
    ("S12000034", "St. Cyrus"): "St Cyrus",
    ("S12000030", "Kings Park"): "King's Park",
    ("S12000027", "Sandsting and Aithsting"): "Aithsting and Sandsting",
    ("S12000027", "Tingwall, Whiteness and Weisdale"): "Whiteness, Weisdale and Tingwall",
    ("S12000017", "Bettyhill, Strathnaver and Altnahara"): "Bettyhill, Strathnaver and Altnaharra",
    ("S12000017", "Spean Bridge, Roy Bridge and Achnacarry CC"): "Spean Bridge, Roy Bridge and Achnacarry",
    ("S12000017", "Fort Augustus and Glenmorriston"): "Fort Augustus and Glenmoriston",
    ("S12000017", "Dunvegan"): "Dunvegan and District",
    ("S12000017", "Stromeferry and Achmore"): "Stromeferry",
    ("S12000017", "Grantown-on-Spey and Vicinity"): "Grantown-on-Spey",
    ("S12000010", "Humbie, East and West Saltoun and Bolton"): "Humbie, East and West Saltoun, Bolton",
    ("S12000010", "Pencaitland and District"): "Pencaitland",
    ("S12000035", "Hunter'S Quay"): "Hunters Quay",
    ("S12000035", "Strachur and District"): "Strachur",
    ("S12000041", "The Royal Burgh of Arbroath and District"): "Royal Burgh of Arbroath",
    ("S12000041", "The Royal Burgh of Montrose"): "Royal Burgh of Montrose",
    ("S12000029", "Sandford and Upper Avondale"): "Sandford/Upper Avondale",
    ("S12000029", "Rigside and Douglas Water"): "Douglas Water and Rigside",
}
UNREGISTERED = {("S12000006", "Stranraer, Ochtrelure and Belmont")}
SKIPPED = ("scheme", "candidates")
ODT = "application/vnd.oasis.opendocument.text"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
TITLES = ("Mr", "Mrs", "Ms", "Miss", "Dr", "Rev")
UNSTATED = "The notice does not state how many places the community council had, so the seat count is not recorded."
UNVOTED = "The council's results notice names the candidates elected and not elected but gives no vote counts."
DUMGAL_PLACES = (
    "The notice does not state the places; the seat count is the maximum membership in Schedule 1 of the council's "
    "scheme, which the notice's candidates did not exceed. Candidates' full forenames are printed run together with "
    "their addresses, so the names here keep the first forename and any initials that follow it."
)
SHORT = "{publisher}'s notice lists {elected} members elected unopposed to {places} places, leaving {left} vacant."


def fetch() -> None:
    for url in pl.read_csv(SOURCES, separator="\t")["url"]:
        if _cached(url):
            continue
        if url in DELIVERED:
            raw.store(
                SOURCE,
                url,
                (INBOX / DELIVERED[url]).read_bytes(),
                suffix=".pdf",
                retrieved_at=datetime.fromtimestamp((INBOX / DELIVERED[url]).stat().st_mtime, UTC),
                media_type="application/pdf",
            )
            continue
        if "web.archive.org" in url:
            time.sleep(10)
        for _ in range(10):
            try:
                response = httpx.get(url, headers={"User-Agent": BROWSER}, timeout=90, follow_redirects=True)
            except httpx.TransportError:
                response = None
            if response is not None and response.status_code != 429:
                break
            time.sleep(90)
        if response is None:
            print(f"unreachable: {url}", file=sys.stderr)
            continue
        if response.content.startswith(b"%PDF"):
            suffix, media = ".pdf", "application/pdf"
        elif response.content.startswith(b"PK") and ODT.encode() in response.content[:100]:
            suffix, media = ".odt", ODT
        elif response.content.startswith(b"PK") and b"word/" in response.content:
            suffix, media = ".docx", DOCX
        elif response.headers.get("content-type", "").startswith("text/html"):
            suffix, media = ".html", "text/html"
        else:
            suffix, media = None, None
        if response.status_code != 200 or suffix is None or media is None:
            print(f"not a document: {url}", file=sys.stderr)
            continue
        raw.store(SOURCE, url, response.content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)


def _cached(url: str) -> bool:
    try:
        raw.cached(SOURCE, url)
    except LookupError:
        return False
    return True


def text_of(url: str, mode: str = "-layout") -> str:
    path = raw.cached(SOURCE, url).path
    if path.suffix == ".html":
        page = re.sub(r"<(script|style)\b.*?</\1>", " ", path.read_text(), flags=re.DOTALL)
        return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", page)).split())
    if path.suffix == ".docx":
        word = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        with zipfile.ZipFile(path) as document:
            body = ElementTree.fromstring(document.read("word/document.xml"))
        return "\n".join(
            " ".join("".join(node.text or "" for node in paragraph.iter(word + "t")).split())
            for paragraph in body.iter(word + "p")
        )
    if path.suffix == ".odt":
        office = "{urn:oasis:names:tc:opendocument:xmlns:%s:1.0}"
        text_ns, table_ns = office % "text", office % "table"
        lines = []

        def spoken(element: ElementTree.Element) -> str:
            parts = [element.text or ""]
            for child in element:
                if child.tag == text_ns + "s":
                    parts.append(" " * int(child.get(text_ns + "c", "1")))
                elif child.tag in (text_ns + "tab", text_ns + "line-break"):
                    parts.append(" ")
                else:
                    parts.append(spoken(child))
                parts.append(child.tail or "")
            return "".join(parts)

        def walk(element: ElementTree.Element) -> None:
            if element.tag == table_ns + "table-row":
                cells = element.iter(table_ns + "table-cell")
                lines.append("\t".join(" ".join(spoken(cell).split()) for cell in cells))
            elif element.tag in (text_ns + "p", text_ns + "h"):
                lines.append(" ".join(spoken(element).split()))
            else:
                for child in element:
                    walk(child)

        with zipfile.ZipFile(path) as document:
            walk(ElementTree.fromstring(document.read("content.xml")))
        return "\n".join(lines)
    return subprocess.run(["pdftotext", mode, str(path), "-"], capture_output=True, text=True, check=True).stdout


def surname_first(printed: str) -> str:
    surname, forenames = (part.strip() for part in printed.split(",", 1))
    return f"{display(forenames) if forenames.isupper() else forenames} {display(surname)}"


def when(text: str) -> date:
    return datetime.strptime(" ".join(text.split()), "%d %B %Y").date()


def proper(printed: str) -> str:
    words = printed.split()
    if len(words) > 1 and words[0].isupper() and not any(word.isupper() for word in words[1:]):
        return " ".join([*(display(word) for word in words[1:]), display(words[0])])
    cut = len(words)
    while cut > 1 and words[cut - 1].isupper():
        cut -= 1
    return (
        " ".join([*words[:cut], display(" ".join(words[cut:])).replace(" Of ", " of ")])
        if cut < len(words)
        else printed
    )


def council_name(lad: str, text: str) -> str:
    text = " ".join(text.split()).replace("&", "and")
    text = re.sub(r"\s+Community Council$", "", text)
    return ALIASES.get((lad, text), text)


def declaration(text: str) -> dict:
    places = re.search(r"Declaration of Result for (\d+) Candidates? to be elected", text)
    head = re.search(r"The Highland Council\s+(.+?)\s+Election Date:\s*\w+\s+(\d+\s+\w+\s+\d{4})", text, re.DOTALL)
    body = re.search(r"Number of Votes\s*\n(.*?)\n\s*And that those", text, re.DOTALL)
    candidates = []
    if head and body:
        for line in body.group(1).splitlines():
            if not line.strip():
                continue
            row = re.fullmatch(r"\s*(.+?)\s{2,}(?:(Yes|YES|No|NO|Elected)\s{2,})?(\d+)\s*", line)
            if row is None:
                raise ValueError(f"unreadable candidate line {line!r}")
            candidates.append(
                {
                    "printed": row.group(1).strip(),
                    "votes": int(row.group(3)),
                    "elected": row.group(2) in ("Yes", "YES", "Elected"),
                }
            )
        council, day = head.group(1), head.group(2)
    else:
        head = re.search(r"The Highland Council\s+(.+?)\s+Declaration of Result", text, re.DOTALL)
        dated = re.search(r"Date:\s*\w+\s+(\d+\s+\w+\s+\d{4})", text)
        body = re.search(r"ELECTED \(\d+\)\s*\n(.*?)\n\s*The \d+ elected candidates", text, re.DOTALL)
        if not (head and dated and body):
            raise ValueError("unreadable declaration")
        rows = list(re.finditer(r"((?:[A-Z][A-Z'-]+ )+)([^\n]+?)\s+(\d+)\s+(Not Elected|Elected)", body.group(1)))
        if re.sub(r"\s+", "", "".join(row.group(0) for row in rows)) != re.sub(r"\s+", "", body.group(1)):
            raise ValueError("unreadable candidate lines")
        for row in rows:
            candidates.append(
                {
                    "printed": f"{row.group(2).strip()} {row.group(1).strip()}",
                    "votes": int(row.group(3)),
                    "elected": row.group(4) == "Elected",
                }
            )
        council, day = head.group(1), dated.group(1)
    if not places:
        raise ValueError("no places")

    def figure(label: str) -> int:
        found = re.search(rf"{label}\s+([\d,]+)\s*$", text, re.MULTILINE) or re.search(
            rf"{label.replace(' Returned', '')}\s+([\d,]+)\s+Returned", text
        )
        if found is None:
            raise ValueError(f"no {label}")
        return int(found.group(1).replace(",", ""))

    return {
        "council": council,
        "day": when(day),
        "places": int(places.group(1)),
        "candidates": candidates,
        "electorate": figure("Electorate"),
        "ballots": figure("Total (?:Number|No) of Postal Votes Returned"),
        "rejected": figure(r"Total Rejected Ballot Papers?"),
        "valid": figure("Total Valid Votes"),
    }


def legacy_notice(text: str) -> dict:
    flat = " ".join(text.split())
    head = re.search(r"ELECTION OF COUNCILLORS FOR (.+?) COMMUNITY COUNCIL", flat, re.IGNORECASE)
    day = re.search(r"(?:Wednesday|at 12\s?noon on|at 12 noon on) (\d+ \w+ \d{4})", flat)
    table = re.search(r"remaining validly nominated\*\s*\n(.*?)\n\s*\*Where", text, re.DOTALL)
    if not (head and day and table):
        raise ValueError("unreadable legacy notice")
    lines = [line for line in table.group(1).splitlines() if line.strip()]
    names, forenames = [], ""
    address_first = lines[0].startswith(" ")
    for line in lines:
        if line.startswith(" "):
            continue
        cells = [cell for cell in re.split(r"\s{2,}", line.strip()) if cell]
        if address_first:
            names.append(" ".join(cells))
        elif len(cells) == 1 and not re.search(r"\d", line):
            forenames = f"{forenames} {cells[0]}".strip()
        else:
            names.append(f"{forenames} {cells[0]}".strip())
            forenames = ""
    if forenames or not names:
        raise ValueError("unreadable legacy member lines")
    return {
        "council": display(head.group(1)).replace(" And ", " and "),
        "day": when(day.group(1)),
        "places": None,
        "members": names,
    }


def perth_notice(text: str) -> dict:
    flat = " ".join(text.split())
    head = re.search(
        r"PERTH AND KINROSS COUNCIL (.+?) COMMUNITY COUNCIL (BY-)?ELECTION \w+ (\d+ \w+ \d{4}) NOTICE", flat
    )
    if head is None:
        raise ValueError("unreadable notice")
    names, places, left = [], None, None
    for line in text.splitlines():
        row = re.fullmatch(r"\s*(?:(\d+)\s{2,})?(.+?), address in community council area(?:\s{2,}(\d+))?\s*", line)
        if row is None:
            continue
        places = int(row.group(1)) if row.group(1) else places
        left = int(row.group(3)) if row.group(3) else left
        names.append(row.group(2))
    if places is None or left is None or places - len(names) != left:
        raise ValueError(f"places {places}, members {len(names)}, and vacancies left {left} do not agree")
    return {
        "council": display(head.group(1)).replace(" And ", " and "),
        "day": when(head.group(3)),
        "places": places,
        "members": names,
        "by": head.group(2) is not None,
    }


def stirling_list(text: str) -> list[dict]:
    day = re.search(r"close of nominations on \w+ (\d+ \w+ \d{4})", " ".join(text.split()))
    start = re.search(r"Established Community Councils[^\n]*\n", text)
    if not (day and start):
        raise ValueError("unreadable list")
    found: dict[str, list[str]] = {}
    current = ""
    for line in text[start.end() :].splitlines():
        cells = [cell for cell in re.split(r"\s{2,}", line.strip()) if cell]
        if not cells:
            continue
        if len(cells) == 2:
            current = cells[0]
            found[current] = []
        elif len(cells) == 1 and current:
            pass
        else:
            raise ValueError(f"unreadable list line {line!r}")
        found[current].append(cells[-1])
    return [
        {"council": council, "day": when(day.group(1)), "places": None, "members": members}
        for council, members in found.items()
    ]


def shetland_list(text: str) -> list[dict]:
    day = re.search(r"(\d+ \w+ \d{4}) \(the day of election\)", " ".join(text.split()))
    table = re.search(r"Address\n(.*?)\n\s*Any person interested", text, re.DOTALL)
    left = re.search(r"Vacancies\n(.*?)\n\s*Returning Officer", text, re.DOTALL)
    if not (day and table and left):
        raise ValueError("unreadable list")
    found: dict[str, dict] = {}
    current = ""
    for line in table.group(1).splitlines():
        cells = [cell for cell in re.split(r"\s{2,}", line.strip()) if cell]
        at = next((i for i, cell in enumerate(cells) if re.fullmatch(r"[A-Z][A-Z' -]+", cell)), None)
        if at is None or len(cells) < at + 3:
            continue
        before = cells[:at]
        if before and before[-1].isdigit():
            current = " ".join(before[:-1])
            found[current] = {"places": int(before[-1]), "members": []}
        elif before:
            renamed = f"{current} {' '.join(before)}"
            found[renamed] = found.pop(current)
            current = renamed
        found[current]["members"].append(f"{cells[at + 1]} {display(cells[at])}")
    remaining = {}
    for line in left.group(1).splitlines():
        cells = [cell for cell in re.split(r"\s{2,}", line.strip()) if cell]
        if len(cells) == 2 and cells[1].isdigit():
            remaining[re.sub(r"\W+", " ", cells[0].replace("&", "and")).lower()] = int(cells[1])
    reads = []
    for council, entry in found.items():
        want = entry["places"] - len(entry["members"])
        if want != remaining.pop(re.sub(r"\W+", " ", council).lower(), 0):
            raise ValueError(f"{council}: vacancies left do not agree with {entry['places']} places")
        reads.append(
            {"council": council, "day": when(day.group(1)), "places": entry["places"], "members": entry["members"]}
        )
    if remaining:
        raise ValueError(f"vacancies listed for unread councils {sorted(remaining)}")
    return reads


def argyll_notices(text: str) -> list[dict]:
    reads = {}
    for chunk in text.split("NOTICE OF UNCONTESTED ELECTION")[1:]:
        flat = " ".join(chunk.split())
        head = re.search(r"COMMUNITY COUNCILLORS FOR (.+?) COMMUNITY COUNCIL ELECTION DAY – (\d+ \w+ \d{4})", flat)
        stated = re.search(r"\n\s*\((\d+)\)\s*(?:\n|$)", chunk)
        rows: list[list] = []
        for line in chunk.splitlines():
            row = re.fullmatch(r" +(\d+)(?: +(\S.*?))?(?: {2,}\S.*)?", line)
            if row:
                rows.append([int(row.group(1)), row.group(2) or ""])
            elif rows and rows[-1][1].endswith(",") and line.strip():
                rows[-1][1] += " " + re.split(r" {2,}", line.strip())[0]
        if not (head and stated and rows):
            raise ValueError("unreadable notice")
        places = int(stated.group(1))
        if [number for number, _ in rows] != list(range(1, places + 1)):
            raise ValueError(f"{head.group(1)}: numbered lines do not run to {places}")
        names = [name for _, name in rows if name]
        read = {
            "council": display(head.group(1).replace("’", "'")).replace(" And ", " and "),
            "day": when(head.group(2)),
            "places": places,
            "members": names,
        }
        if reads.setdefault(read["council"], read) != read:
            raise ValueError(f"{read['council']}: two different notices")
    return list(reads.values())


def argyll_declaration(text: str) -> dict:
    head = re.search(r"DECLARATION OF RESULT (.+?) COMMUNITY COUNCIL DATE OF ELECTION - (\d+ \w+ \d{4})", text)
    body = re.search(r"Number of Votes Cast Result (.+?) And I do hereby declare", text)
    rejected = re.search(r"ballot papers rejected totalled (\d+)", text)
    received = re.search(r"number of ballot papers received was ([\d,]+)", text)
    if not (head and body and rejected and received):
        raise ValueError("unreadable declaration")
    rows = list(
        re.finditer(
            r"([A-Z][A-Z’'-]+(?: [A-Z][A-Z’'-]+)*, .+?) (\d+)( ELECTED)?(?= [A-Z][A-Z’'-]+[ ,]|$)", body.group(1)
        )
    )
    if " ".join(row.group(0) for row in rows) != body.group(1):
        raise ValueError("unreadable candidate lines")
    candidates = [
        {
            "printed": row.group(1),
            "name": surname_first(row.group(1)),
            "votes": int(row.group(2)),
            "elected": bool(row.group(3)),
        }
        for row in rows
    ]
    return {
        "council": display(head.group(1)).replace(" And ", " and "),
        "day": when(head.group(2)),
        "places": sum(1 for candidate in candidates if candidate["elected"]),
        "candidates": candidates,
        "electorate": None,
        "ballots": int(received.group(1).replace(",", "")),
        "rejected": int(rejected.group(1)),
        "valid": None,
    }


def clacks_notice(text: str) -> dict:
    head = re.search(r"UNCONTESTED ELECTION\s+(.+?)\s+COMMUNITY COUNCIL\s", text, re.DOTALL)
    body = re.search(r"\n\s*Name\s*\n(.*?)\n\s*\.{10,}", text, re.DOTALL)
    day = re.search(r"Depute Returning Officer\s+(\d+ \w+ \d{4})", text)
    if not (head and body and day):
        raise ValueError("unreadable notice")
    return {
        "council": display(" ".join(head.group(1).split())).replace(" And ", " and "),
        "day": when(day.group(1)),
        "places": None,
        "members": [line.strip() for line in body.group(1).splitlines() if line.strip()],
    }


def clacks_declaration(text: str) -> dict:
    head = re.search(r"CONTESTED ELECTION\s+(.+?) COMMUNITY COUNCIL\s", text)
    body = re.search(r"Candidate Name\s+Votes Cast Result\s*\n(.*?)\n\s*\*", text, re.DOTALL)
    rejected = re.search(r"Rejected Votes\s+(\d+)", text)
    received = re.search(r"No\. Ballot Papers\s+([\d,]+)\s*\n\s*Received", text)
    day = re.search(r"Date:\s*(\d+ \w+ \d{4})", text)
    if not (head and body and rejected and received and day):
        raise ValueError("unreadable declaration")
    candidates, tied = [], []
    for line in body.group(1).splitlines():
        row = re.fullmatch(r"\s*(.+?)\s{2,}(\d+)\s+(Elected|Not Elected)\s*(\*)?\s*", line)
        if row is None:
            raise ValueError(f"unreadable candidate line {line!r}")
        candidates.append({"printed": row.group(1), "votes": int(row.group(2)), "elected": row.group(3) == "Elected"})
        if row.group(4):
            tied.append(row.group(1))
    return {
        "council": display(head.group(1)).replace(" And ", " and "),
        "day": when(day.group(1)),
        "places": sum(1 for candidate in candidates if candidate["elected"]),
        "candidates": candidates,
        "electorate": None,
        "ballots": int(received.group(1).replace(",", "")),
        "rejected": int(rejected.group(1)),
        "valid": None,
        "tied": tied,
    }


def lothian_list(text: str) -> list[dict]:
    day = re.search(r"duly elected from (\d+ \w+ \d{4})", text)
    if day is None:
        raise ValueError("unreadable list")
    reads: list[dict] = []
    lines: list[str] = []
    for line in [line.strip() for line in text[day.end() :].splitlines()[1:] if line.strip()]:
        stated = re.fullmatch(r"\((\d+) places? – (\d+) vacanc(?:y|ies)\)", line)
        if stated is None:
            lines.append(line)
            continue
        council = lines.pop()
        if council == "Community Council":
            council = f"{lines.pop()} {council}"
        if glued := re.fullmatch(r"(.*[a-z])([A-Z].* Community Council)", council):
            lines.append(glued.group(1))
            council = glued.group(2)
        if reads:
            reads[-1]["members"] = lines
        elif lines:
            raise ValueError(f"names before the first council {lines}")
        lines = []
        reads.append(
            {
                "council": council,
                "day": when(day.group(1)),
                "places": int(stated.group(1)),
                "left": int(stated.group(2)),
                "members": [],
            }
        )
    if not reads:
        raise ValueError("unreadable list")
    reads[-1]["members"] = lines
    for read in reads:
        if read["places"] - len(read["members"]) != read.pop("left"):
            raise ValueError(
                f"{read['council']}: places and vacancies do not agree with {len(read['members'])} members"
            )
    return reads


def lothian_results(text: str) -> dict:
    head = re.search(r"ELECTIONS - (\d+ \w+ \d{4})\s+RESULTS OF THE ELECTION FOR (.+?) COMMUNITY COUNCIL", text)
    body = re.search(r"\n\s*Elected\s*\n(.*?)\n\s*Not Elected\s*\n(.*?)\n\s*\d+ \w+ \d{4}", text, re.DOTALL)
    if not (head and body):
        raise ValueError("unreadable results")
    candidates = [
        {"printed": line.strip(), "name": surname_first(line.strip()), "votes": None, "elected": elected}
        for elected, block in ((True, body.group(1)), (False, body.group(2)))
        for line in block.splitlines()
        if line.strip()
    ]
    return {
        "council": display(head.group(2)),
        "day": when(head.group(1)),
        "places": sum(1 for candidate in candidates if candidate["elected"]),
        "candidates": candidates,
        "unvoted": True,
    }


def angus_notice(text: str) -> list[dict]:
    day = re.search(r"Returning Officer \(Community Council Elections\)\s+(\d+ \w+ \d{4})", text)
    if day is None:
        raise ValueError("unreadable notice")
    reads: list[dict] = []
    columns: list[int] = []
    for line in text.splitlines():
        if "Community Council Area" in line and "surname" in line:
            columns = [line.index(label) for label in ("Number", "Candidate’s surname", "Other names", "Candidate’s")]
            columns[3] = line.index("Candidate’s", columns[1] + 1)
            continue
        if not columns or not line.strip() or "Printed and published" in line:
            continue
        if re.match(r"\s*\d\.", line):
            columns = []
            continue
        cells = [(found.start(), found.group(0)) for found in re.finditer(r"\S+(?: \S+)*", line)]
        sorted_cells: list[list[str]] = [[], [], [], [], []]
        for start, cell in cells:
            sorted_cells[min(range(5), key=lambda i: abs(start - ([0, *columns])[i]))].append(cell)
        area, places, surname, forenames, _ = (" ".join(cell) for cell in sorted_cells)
        if not surname and not forenames:
            continue
        if area:
            if not places.isdigit():
                raise ValueError(f"unreadable area line {line!r}")
            reads.append({"council": area, "day": when(day.group(1)), "places": int(places), "members": []})
        if not (surname and forenames and reads):
            raise ValueError(f"unreadable candidate line {line!r}")
        reads[-1]["members"].append({"printed": f"{surname}, {forenames}", "name": f"{forenames} {surname}"})
    return reads


def notice(text: str) -> dict:
    if "PERTH AND KINROSS COUNCIL" in text:
        return perth_notice(text)
    if "ELECTION OF COUNCILLORS FOR" in text:
        return legacy_notice(text)
    head = re.search(
        r"Election Of Community Councillors for\s+(.+?)\s+(?:Election Day.*?)?NOTICE IS HERE?BY GIVEN",
        text,
        re.DOTALL | re.IGNORECASE,
    ) or re.search(r"Notice of Uncontested Election\s+(.+? Community Council)\s", text)
    day = re.search(r"with effect from (?:12 noon on )?\w+\s+(\d+\s+\w+\s+\d{4})", " ".join(text.split()))
    table = re.search(r"\n([^\n]*Forename[^\n]*)\n(.*?)\n\s*\*Where", text, re.DOTALL)
    if not (head and day and table):
        raise ValueError("unreadable notice")
    header = table.group(1)
    surname_first = header.index("Surname") < header.index("Forename")
    total = "Membership" in text[: table.end(1)]
    received = "Nominations" in text[: table.end(2)]
    names, places, left = [], None, None
    for line in table.group(2).splitlines():
        cells = [cell for cell in re.split(r"\s{2,}", line.strip()) if cell]
        if not cells or cells[0].startswith(("Vacancies", "Membership", "remaining", "Received")):
            continue
        if all(re.fullmatch(r"\(\d+/\d+/\d{4}\)", cell) for cell in cells):
            continue
        numbers = []
        while cells and cells[0].isdigit():
            numbers.append(int(cells.pop(0)))
        if numbers:
            places = numbers[-1]
            if len(numbers) != (2 if total else 1):
                raise ValueError(f"unexpected leading figures {numbers}")
        if cells and cells[-1].isdigit():
            left = int(cells.pop())
        if len(cells) != 2:
            raise ValueError(f"unreadable member line {line!r}")
        surname, forename = cells if surname_first else cells[::-1]
        names.append(f"{forename} {surname}")
    if received:
        if left != 0 or not names:
            raise ValueError(f"nominations received with {left} vacancies left")
        return {"council": head.group(1), "day": when(day.group(1)), "places": None, "members": names}
    if places is not None and left == places == len(names):
        left = 0
    if places is None or left is None or places - len(names) != left:
        raise ValueError(f"places {places}, members {len(names)}, and vacancies left {left} do not agree")
    return {"council": head.group(1), "day": when(day.group(1)), "places": places, "members": names}


def lanarkshire_results(text: str) -> dict:
    head = re.search(
        r" ([A-Z][^:]*?) Community Council Election \d{4} \1 Community Council Election (?:\d{4} )?\w+day "
        r"(\d+ \w+ \d{4}) Results? of Ballot",
        text,
    )
    body = re.search(r"Candidate Votes Elected (.*?) and I declare", text)
    if not (head and body):
        raise ValueError("unreadable results page")
    rows = list(re.finditer(r"(.+?) ?, (.+?) (\d+) (Yes|No)(?: |$)", body.group(1)))
    if "".join(row.group(0) for row in rows).strip() != body.group(1):
        raise ValueError("unreadable results table")
    candidates = [
        {
            "printed": f"{row.group(1)}, {row.group(2)}",
            "name": f"{row.group(2)} {row.group(1)}",
            "votes": int(row.group(3)),
            "elected": row.group(4) == "Yes",
        }
        for row in rows
    ]
    return {
        "council": head.group(1),
        "day": when(head.group(2)),
        "places": sum(1 for candidate in candidates if candidate["elected"]),
        "candidates": candidates,
    }


def lanarkshire_list(text: str) -> list[dict]:
    sections = re.split(r"\n(?=[^\n]+ Community Council - Election Results of Uncontested Election\n)", text)
    dates = [when(day) for day in re.findall(r"elected to the Community Council as from \w+ (\d+ \w+ \d{4})", text)]
    day = max(set(dates), key=dates.count)
    reads = []
    for section in sections[1:]:
        head = re.match(r"(.+?) Community Council - Election", section)
        counts = re.search(
            r"there were (\d+) candidates.? nomination papers received\. There is provision for (\d+) elected places.*?"
            r"as from \w+ (\d+ \w+ \d{4})",
            section,
        )
        if not (head and counts):
            raise ValueError("unreadable notice section")
        members = []
        for number, printed in re.findall(r"^(\d+)\t(.+)$", section, re.MULTILINE):
            title, name = printed.split(" ", 1)
            if int(number) != len(members) + 1 or title not in TITLES:
                raise ValueError(f"unreadable member line {printed!r}")
            members.append({"printed": printed, "name": name})
        received, places = int(counts.group(1)), int(counts.group(2))
        if len(members) != received or received > places:
            raise ValueError(f"{head.group(1)}: {len(members)} members, {received} nominated, {places} places")
        stated = when(counts.group(3))
        reads.append(
            {
                "council": head.group(1),
                "day": day,
                "places": places,
                "members": members,
                "caveat": None
                if stated == day
                else f"The council's 2022 notice dates this council's members as elected from {stated:%-d %B %Y}, "
                f"but it is headed 2022, the council lists this council among its 2022 uncontested elections, and "
                f"every other council in the notice is dated {day:%-d %B %Y}, so the election is dated {day:%-d %B %Y}.",
            }
        )
    return reads


DUMGAL_SURNAME = r"((?:Ma?c)?[A-Z][A-Z'’-]+(?: (?:[A-Z][A-Z'’-]+|\([A-Z ]+\)))*)"


def dumgal_name(entry: str, address: bool) -> dict:
    found = re.fullmatch(DUMGAL_SURNAME + r"(?:,\s*|\s+(?=[A-Z][a-z]))(.+)", entry.strip().lstrip("*"))
    if found is None:
        raise ValueError(f"unreadable name {entry!r}")
    surname, parts = found.group(1), [part.strip() for part in re.split(r",\s*", found.group(2)) if part.strip()]
    if address:
        kept = [re.split(r"\s+(?=\S*\d)", parts[0])[0]]
        for part in parts[1:]:
            if not re.fullmatch(r"(?:[A-Z]\.?\s?)+", part):
                break
            kept.append(part)
        parts = kept
    forenames = " ".join(parts)
    plain = re.sub(r"\s*\(.*?\)", "", surname)
    return {"printed": f"{surname}, {forenames}", "name": f"{forenames} {display(plain).replace(' Of ', ' of ')}"}


def dumgal_title(text: str) -> str:
    words = text.split()
    return " ".join(
        word.lower() if index and word.lower() in ("and", "of", "the") else display(word) if word.isupper() else word
        for index, word in enumerate(words)
    )


def dumgal_notice(text: str) -> list[dict]:
    day = re.search(r"(?:ORDINARY ELECTIONS\s*[–-]|DATE OF ELECTION:)\s*\w+,?\s+(\d+ \w+ \d{4})", text)
    if day is None:
        raise ValueError("unreadable notice")
    reads: list[dict] = []
    started, label, previous = False, [], ""
    lines = text.splitlines()
    for number, line in enumerate(lines):
        if "Returning Officer" in line:
            while reads and not reads[-1]["members"]:
                reads.pop()
            break
        if "Validly Nominated Candidates" in line:
            started = True
            continue
        cells = re.split(r"\s{2,}", line.strip())
        if not started or not line.strip() or cells[0] in ("PUBLIC", "OFFICIAL") or cells[0].lower() == "electors":
            continue
        if re.match(r"\S", line) or len(line) - len(line.lstrip()) < 6:
            left, right = cells[0], " ".join(cells[1:])
            if joined := re.search(r"(?<=[a-z]) (?=" + DUMGAL_SURNAME + ",)", left):
                left, right = left[: joined.start()], f"{left[joined.end() :]} {right}".strip()
        else:
            left, right = "", " ".join(cells)
        if left:
            if not previous:
                label = []
                reads.append({"council": "", "day": when(day.group(1)), "places": None, "members": []})
            label.append(left)
            reads[-1]["council"] = dumgal_title(" ".join(label))
        previous = left
        if right and re.match(DUMGAL_SURNAME + r"(?:,|\s+[A-Z][a-z])", right):
            if not reads:
                raise ValueError(f"unreadable line {line!r}")
            reads[-1]["members"].append(dumgal_name(right, address=True))
    if empty := [read["council"] for read in reads if not read["members"]]:
        raise ValueError(f"no members for {empty}")
    return reads


def dumgal_declaration(text: str) -> dict:
    head = re.search(r"NOTICE OF COMMUNITY COUNCILLORS ELECTED\s+AS RESULT OF POLL\s+HELD ON (\d+ \w+ \d{4})", text)
    council = re.search(
        r"Election of (\d+) Community Councillors to serve on\s+(.+?)\s+Community Council,", text, re.DOTALL
    )
    body = re.search(r"NAME\s+VOTES\s*\n(.*?)\n\s*Number of Ballot Papers Received", text, re.DOTALL)
    chosen = re.search(r"are\s+as\s+follows:(.*?)Ballot Papers rejected:", text, re.DOTALL)
    if not (head and council and body and chosen):
        raise ValueError("unreadable declaration")
    candidates = []
    for line in body.group(1).splitlines():
        row = re.fullmatch(r"\s*(.+?)\s{2,}(\d+)\s*", line)
        if row:
            candidates.append({**dumgal_name(row.group(1), address=False), "votes": int(row.group(2))})
        elif line.strip() and not re.fullmatch(r"\s*(OFFICIAL|.*COMMUNITY COUNCIL|THURSDAY .*)\s*", line):
            raise ValueError(f"unreadable candidate line {line!r}")
    elected = {
        dumgal_name(line.strip(), address=False)["printed"].rstrip(".")
        for line in re.findall(r"^\s*(?:\d+\s+)?(\*?" + DUMGAL_SURNAME[1:-1] + r",.*)$", chosen.group(1), re.MULTILINE)
    }
    for candidate in candidates:
        candidate["elected"] = str(candidate["printed"]).rstrip(".") in elected
    if len(elected) != sum(candidate["elected"] for candidate in candidates):
        raise ValueError("elected members not among the candidates")

    def figure(label: str) -> int:
        found = re.search(rf"{label}\s+(\d+)", text) or re.search(rf"(\d+)\s*\n\s*{label}", text)
        if found is None:
            raise ValueError(f"no {label}")
        return int(found.group(1))

    tied = []
    if "equality of votes" in text:
        lowest = min(candidate["votes"] for candidate in candidates if candidate["elected"])
        tied = [candidate["printed"] for candidate in candidates if candidate["votes"] == lowest]
    return {
        "council": council.group(2),
        "day": when(head.group(1)),
        "places": int(council.group(1)),
        "candidates": candidates,
        "electorate": figure(r"Electorate(?: \(from total in Register\))?"),
        "ballots": figure("Number of Ballot Papers Received"),
        "rejected": figure("Number of Ballot Papers rejected"),
        "valid": figure("Total number of valid Ballot Papers"),
        "tied": tied,
        "settled": "the declaration records that the tie was settled by drawing lots",
    }


def dumgal_casual(text: str) -> dict:
    head = re.search(r"COUNCILLORS FOR\s+(.+?) COMMUNITY COUNCIL\s+DATE OF ELECTION \w+ (\d+ \w+ \d{4})", text)
    names = re.search(r"Names of Candidates? remaining validly nominated\n(.*?)\nRO", text, re.DOTALL)
    if not (head and names):
        raise ValueError("unreadable casual vacancy notice")
    members = [
        dumgal_name(line, address=False)
        for line in names.group(1).splitlines()
        if re.match(DUMGAL_SURNAME + ",", line) and not line.startswith("Address")
    ]
    return {
        "council": dumgal_title(head.group(1)),
        "day": when(head.group(2)),
        "places": None,
        "members": members,
        "by": True,
    }


def dumgal_schedule(text: str) -> dict[str, tuple[int, int]]:
    rows = re.findall(r"^\s*\d+ (\S.*?)\s{2,}([\d,]+)\s+(\d+)\s+\d+\s*$", text, re.MULTILINE)
    return {
        slug(council_name("S12000006", name)): (int(voters.replace(",", "")), int(places))
        for name, voters, places in rows
    }


def page_html(url: str) -> str:
    return re.sub(r"<(script|style)\b.*?</\1>", " ", raw.cached(SOURCE, url).path.read_text(), flags=re.DOTALL)


def flat(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def aberdeenshire_page(page: str) -> list[dict]:
    reads: list[dict] = []
    balloted = set()
    for ballot in re.finditer(
        r"<p>A ballot for (.+?) Community Council took place on (.+?), due to.*?</table>(.*?)<h", page, re.DOTALL
    ):
        candidates, elected, note = [], True, None
        for row in re.findall(r"<tr>(.*?)</tr>", ballot.group(0), re.DOTALL):
            cells = [flat(cell) for cell in re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL)]
            if cells in (["Not elected"], ["Not selected"]):
                elected = False
            elif cells == ["Candidate", "Total votes"]:
                continue
            elif len(cells) == 2 and cells[1].isdigit():
                candidates.append({**dumgal_name(cells[0], address=False), "votes": int(cells[1]), "elected": elected})
            elif len(cells) == 2 and (unopposed := re.fullmatch(r"N/A \(elected unopposed for (\w+) area\)", cells[1])):
                person = dumgal_name(cells[0], address=False)["name"]
                note = (
                    f"The ballot was held in the Ballater area only; {person} was elected unopposed for the "
                    f"{unopposed.group(1)} area and is not counted among this contest's candidates."
                )
            else:
                raise ValueError(f"unreadable ballot row {cells}")
        tied = []
        if "determined by lot" in ballot.group(3):
            lowest = min(candidate["votes"] for candidate in candidates if candidate["elected"])
            tied = [candidate["printed"] for candidate in candidates if candidate["votes"] == lowest]
        council = " ".join(ballot.group(1).split())
        balloted.add(council)
        reads.append(
            {
                "council": council,
                "day": when(re.sub(r"(\d+)(?:st|nd|rd|th)", r"\1", flat(ballot.group(2)))),
                "places": sum(candidate["elected"] for candidate in candidates),
                "candidates": candidates,
                "tied": tied,
                "settled": "the council's area page records that the last place was decided by lot",
                "caveat": note,
            }
        )
    for council, listed in re.findall(
        r"<h3[^>]*>((?:(?!</h3>).)*)</h3>\s*<p[^>]*>((?:(?!</p>).)*)</p>", page, re.DOTALL
    ):
        council = re.sub(r"\s+Community Council$", "", flat(council))
        listed = flat(listed)
        if not listed.startswith("2024 election:"):
            continue
        listed = listed.removeprefix("2024 election:").strip()
        if listed.startswith("not formed") or council in balloted:
            continue
        reads.append(
            {
                "council": council,
                "day": date(2024, 9, 20),
                "places": None,
                "members": [name.strip() for name in re.split(r",\s*|\s+and\s+", listed.rstrip(". ")) if name.strip()],
                "caveat": "The council's area page lists the members elected in 2024 without a date; the council's "
                "news release of 13 September 2024 says nominees would be formally elected on 20 September 2024.",
            }
        )
    return reads


def borders_news(page: str, candidates_page: str) -> dict:
    head = re.search(r"election that took place on \w+ (\d+ \w+ \d{4})\.", page)
    counts = re.search(
        r"(\d+) candidates contested for the (\d+) available places on the (.+?) Community Council", page
    )
    elected = [
        flat(item)
        for item in re.findall(r"<li>(.*?)</li>", page.split("declared to be elected", 1)[-1].split("</ul>")[0])
    ]
    standing = re.search(r"are as follows:(.*?)Any enquiries", flat(candidates_page))
    if not (head and counts and standing and elected):
        raise ValueError("unreadable news item")
    names = re.findall(r"[A-Z][a-z]+ [A-Z][a-z]+", standing.group(1))
    if len(names) != int(counts.group(1)) or not set(elected) <= set(names):
        raise ValueError("candidate list does not match")
    return {
        "council": counts.group(3),
        "day": when(head.group(1)),
        "places": int(counts.group(2)),
        "candidates": [{"printed": name, "votes": None, "elected": name in elected} for name in names],
        "unvoted": True,
        "unvoted_note": "The council's results release names the 10 candidates elected, and its earlier ballot "
        "notice names all 20 who stood, but neither gives vote counts.",
    }


def counted(url: str, read: dict) -> list[tuple[dict, list[dict]]]:
    elected = sum(candidate["elected"] for candidate in read["candidates"])
    if elected != min(read["places"], len(read["candidates"])):
        raise ValueError(f"{url}: {elected} elected to {read['places']} places")
    if read.get("valid") is not None and read["ballots"] != read["valid"] + read["rejected"]:
        raise ValueError(f"{url}: ballots do not add up")
    if not read.get("unvoted"):
        ranked = sorted(read["candidates"], key=lambda candidate: -candidate["votes"])
        if {c["printed"] for c in ranked[:elected]} != {c["printed"] for c in ranked if c["elected"]}:
            raise ValueError(f"{url}: the elected candidates are not the top polling ones")
    return [(read, read["candidates"])]


def tacit(reads: list[dict]) -> list[tuple[dict, list[dict]]]:
    return [
        (
            read,
            [
                {"votes": None, "elected": True, **(member if isinstance(member, dict) else {"printed": member})}
                for member in read["members"]
            ],
        )
        for read in reads
    ]


def transcribed(url: str) -> list[dict]:
    frame = pl.read_csv(TRANSCRIBED, separator="\t", infer_schema=False).filter(pl.col("url") == url)
    if frame.is_empty():
        raise ValueError("no transcription")
    return [
        {
            "council": row["council"],
            "day": date.fromisoformat(row["day"]),
            "places": int(row["places"]) if row["places"] else None,
            "members": row["members"].split("; "),
        }
        for row in frame.iter_rows(named=True)
    ]


def companion(lad: str, kind: str) -> str:
    frame = pl.read_csv(SOURCES, separator="\t").filter((pl.col("lad") == lad) & (pl.col("kind") == kind))
    return frame["url"].item()


def dumgal_reads(url: str, kind: str, text: str) -> list[tuple[dict, list[dict]]]:
    if kind == "casual":
        return tacit([dumgal_casual(text)])
    schedule = dumgal_schedule(text_of(companion("S12000006", "scheme")))
    if kind == "declaration":
        read = dumgal_declaration(text)
        voters, places = schedule[slug(council_name("S12000006", read["council"]))]
        if places != read["places"]:
            raise ValueError(f"{read['council']}: {read['places']} places, the scheme gives {places}")
        if read["electorate"] == voters:
            read["caveat"] = (
                "The electorate in the declaration equals the scheme's count of registered voters at 1 September 2017."
            )
        return counted(url, read)
    reads = dumgal_notice(text)
    for read in reads:
        council = council_name("S12000006", read["council"])
        if ("S12000006", council) in UNREGISTERED:
            continue
        read["places"] = schedule[slug(council)][1]
        if len(read["members"]) > read["places"]:
            raise ValueError(f"{council}: {len(read['members'])} members, {read['places']} places")
        read["caveat"] = DUMGAL_PLACES
    return tacit(reads)


def reads_of(url: str, lad: str, kind: str) -> list[tuple[dict, list[dict]]]:
    if kind == "transcribed":
        return tacit(transcribed(url))
    if lad == "S12000034":
        reads = aberdeenshire_page(page_html(url))
        return [pair for read in reads for pair in (counted(url, read) if "candidates" in read else tacit([read]))]
    if lad == "S12000026":
        return counted(url, borders_news(page_html(url), page_html(companion(lad, "candidates"))))
    text = text_of(url)
    if lad == "S12000006":
        return dumgal_reads(url, kind, text)
    if lad == "S12000035":
        if kind == "page":
            return counted(url, argyll_declaration(text))
        return tacit(
            [
                {**read, "members": [{"printed": m, "name": surname_first(m)} for m in read["members"]]}
                for read in argyll_notices(text)
            ]
        )
    if lad == "S12000005":
        return counted(url, clacks_declaration(text)) if kind == "declaration" else tacit([clacks_notice(text)])
    if lad == "S12000010":
        if kind == "declaration":
            return counted(url, lothian_results(text))
        return tacit(lothian_list(text_of(url, "-raw")))
    if lad == "S12000041":
        return tacit(angus_notice(text))
    if lad == "S12000029":
        return counted(url, lanarkshire_results(text)) if kind == "results" else tacit(lanarkshire_list(text))
    if kind == "declaration":
        return counted(url, declaration(text))
    if kind == "list":
        return tacit(stirling_list(text) if lad == "S12000030" else shetland_list(text))
    return tacit([notice(text)])


def parse() -> None:
    rows = []
    for url, lad, kind in pl.read_csv(SOURCES, separator="\t").rows():
        if lad not in READ or kind in SKIPPED:
            continue
        if not _cached(url):
            print(f"not fetched: {url}", file=sys.stderr)
            continue
        try:
            reads = reads_of(url, lad, kind)
        except ValueError as error:
            if kind != "notice" or lad != HIGHLAND:
                raise ValueError(f"{url}: {error}") from error
            print(f"unreadable notice: {url}: {error}", file=sys.stderr)
            continue
        for read, candidates in reads:
            council = council_name(lad, read["council"])
            if (lad, council) in UNREGISTERED:
                continue
            for candidate in candidates:
                rows.append(
                    {
                        "url": url,
                        "lad": lad,
                        "council": council,
                        "key": f"{lad}-{slug(council)}",
                        "day": read["day"],
                        "places": read["places"],
                        "by": read.get("by", False),
                        "printed": candidate["printed"],
                        "name": candidate.get("name") or proper(str(candidate["printed"])),
                        "votes": candidate["votes"],
                        "elected": candidate["elected"],
                        "electorate": read.get("electorate"),
                        "ballots": read.get("ballots"),
                        "rejected": read.get("rejected"),
                        "valid": read.get("valid"),
                        "unvoted": read.get("unvoted", False),
                        "tied": candidate["printed"] in read.get("tied", ()),
                        "settled": read.get(
                            "settled", "the declaration records that the tie was settled by cutting a pack of cards"
                        ),
                        "unvoted_note": read.get("unvoted_note", UNVOTED),
                        "caveat": read.get("caveat"),
                    }
                )
    frame = pl.DataFrame(rows, infer_schema_length=None)
    with connect() as conn:
        known = {
            code
            for (code,) in conn.execute(
                "select c.code from area a join area_code c on c.area_id = a.id where a.scheme_code = %s", [SCHEME]
            ).fetchall()
        }
    if missing := sorted(set(frame["key"]) - known):
        raise ValueError(f"councils not in the register: {missing}")
    if clash := frame.group_by("key", "day").agg(pl.col("url").n_unique()).filter(pl.col("url") > 1).rows():
        raise ValueError(f"two sources for one election: {clash}")
    summary = frame.group_by("day").agg(pl.col("key").n_unique()).sort("day").rows()
    print(f"{frame['key'].n_unique()} councils, {frame.height} candidates; elections by day {summary}", file=sys.stderr)
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Community council elections under council schemes of establishment: Highland 2015 to 2026 "
            "(postal ballots from the returning officer's declarations of result), Argyll and Bute's Cove and "
            "Kilcreggan and Clackmannanshire's Dollar polls, East Lothian's Dunbar result, and councils formed "
            "unopposed in Perth and Kinross, Stirling, Shetland, Argyll and Bute, Clackmannanshire, East Lothian, "
            "Moray, Angus, and South Lanarkshire from the councils' notices and lists, as tacit sets, with South "
            "Lanarkshire's 2026 polls from the council's results pages; Dumfries and Galloway's October 2024 round "
            "(76 councils formed unopposed and 4 polls) and the Dalry casual vacancy of 2025, Aberdeenshire's September "
            "2024 round (62 formed unopposed and the Ballater and Crathie and Kemnay ballots), and Scottish Borders' "
            "Coldingham ballot of 2023",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        systems = {code: cs.key("gb.electoral_system", code) for code in ("fptp", "block")}
        if any(system in cs.minted for system in systems.values()):
            raise ValueError("publish gb.parliament first")
        schemes = {}
        for lad, (publisher, title, url, _) in COUNCILS.items():
            if lad not in set(frame["lad"]):
                continue
            schemes[lad] = cs.source(publisher, title)
            cs.add("source", id=schemes[lad], kind="official_body", publisher=publisher, title=title, url=url)
        sources: dict[str, UUID] = {}
        for url, lad, kind in pl.read_csv(SOURCES, separator="\t").rows():
            if url not in set(frame["url"]):
                continue
            first = frame.filter(pl.col("url") == url).row(0, named=True)
            label = {
                "declaration": "Declaration of Result",
                "results": "Results of Ballot",
                "list": "Notice of Results of Uncontested Election"
                if lad == "S12000029"
                else "List of Members Elected",
            }.get(kind, "Notice of Uncontested Election")
            title = f"{first['council']} Community Council, {label}, {first['day']:%-d %B %Y}"
            publisher = COUNCILS[lad][0]
            sources[url] = cs.source(publisher, title)
            cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        paths = dict(
            conn.execute(
                """select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
                   where e.scheme = 'gb.polity' and e.value = any(%s)""",
                [sorted(set(frame["key"]))],
            ).fetchall()
        )
        bodies = dict(
            conn.execute(
                """select e.value, n.text from external_id e join name n on n.entity_id = e.entity_id and n.is_preferred
                   where e.scheme = 'gb.body' and e.value = any(%s)""",
                [sorted(set(frame["key"]))],
            ).fetchall()
        )
        people: dict[str, UUID] = {}
        units: set[UUID] = set()
        for (key, day), lines in frame.group_by("key", "day", maintain_order=True):
            assert isinstance(day, date)
            first = lines.row(0, named=True)
            lad, url, places = first["lad"], first["url"], first["places"]
            tacit = first["votes"] is None and not first["unvoted"]
            polity = cs.key("gb.polity", key)
            body = cs.key("gb.body", key)
            area = cs.area(SCHEME, key)
            unit = cs.unit(SCHEME, key)
            if unit in cs.minted and unit not in units:
                units.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=SCHEME,
                    code=key,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, first["council"])
            contest_key = f"{day}:cc:{key}"
            election = cs.key("gb.election", contest_key)
            cs.add("election", id=election, polity_id=polity, kind="local", date_from=day, date_to=day, status="held")
            name(election, f"{day.year} {bodies[key]} election")
            election_path = f"{paths[key]}/{day}-local"
            cs.add("slug", path=election_path, entity_id=election)
            cs.add("note", entity_id=election, kind="context", body_md=COUNCILS[lad][3])
            if first["by"]:
                cs.add(
                    "note",
                    entity_id=election,
                    kind="context",
                    body_md="This was a by-election to fill vacancies on an existing community council.",
                )
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=body,
                district_area_id=area,
                magnitude=places,
                round_number=1,
                electoral_system_id=systems["fptp" if places == 1 else "block"],
                default_result_kind="tacit" if tacit else "counted",
            )
            name(contest, f"{bodies[key]}, {day.year}")
            cs.add("slug", path=f"{election_path}/council", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=sources[url])
            cs.add("citation", entity_id=contest, source_id=schemes[lad])
            result_set = cs.key("gb.cc_elections.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=sources[url],
                is_default=True,
                completeness=0 if first["unvoted"] else 1,
            )
            if first["unvoted"]:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=first["unvoted_note"])
            if tied := lines.filter(pl.col("tied"))["name"].to_list():
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{' and '.join(tied)} tied on {lines.filter(pl.col('tied'))['votes'][0]} votes; {first['settled']}.",
                )
            if first["caveat"]:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=first["caveat"])
            if tacit and places is None:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNSTATED)
            elif tacit and lines.height < places:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=SHORT.format(
                        publisher=COUNCILS[lad][0].removeprefix("The "),
                        elected=lines.height,
                        places=places,
                        left=places - lines.height,
                    ),
                )
            for number, line in enumerate(lines.iter_rows(named=True), start=1):
                option_key = f"{contest_key}:{number}"
                person_key = f"{key}:{slug(line['name'])}"
                if person_key not in people:
                    people[person_key] = cs.key("gb.cc_elections.person", person_key)
                    cs.add("person", id=people[person_key])
                    name(people[person_key], line["name"])
                option = cs.key("gb.cc_elections.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None,
                    ballot_label=line["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key("gb.cc_elections.candidacy", option_key),
                    person_id=people[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=line["printed"],
                    member_of_party_id=None,
                    is_independent=False,
                )
                if line["votes"] is not None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=VOTES,
                        count_round=0,
                        flags=0,
                        value=line["votes"],
                    )
                if line["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
            if tacit or first["unvoted"]:
                continue
            for measure, field in (
                (REGISTERED, "electorate"),
                (BALLOTS, "ballots"),
                (VALID, "valid"),
                (INVALID, "rejected"),
            ):
                if first[field] is None:
                    continue
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=first[field],
                )
        return cs.write()
