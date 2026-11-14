import csv
import functools
import html
import io
import re
import struct
import subprocess
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path
from urllib.parse import urljoin
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.names import display
from ea.sources.gb.cc_elections import proper, surname_first
from ea.sources.gb.parish import fold, slug
from ea.store import DATA, connect

SOURCE = "gb.welsh_communities"
VERSION = "1"
WARDS = "gb.parish_ward"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
DIGITIZED = 8
DAY = date(2022, 5, 5)
BROWSER = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0 Safari/537.36"
VOTES, REGISTERED, INVALID = 1, 2, 5
COUNCILS = {
    "W06000020": (
        "Torfaen County Borough Council",
        "Community Council Elections 2022",
        "https://www.torfaen.gov.uk/en/about-the-council/elections/elections-results/community-council",
        "torfaen",
    ),
    "W06000010": (
        "Carmarthenshire County Council",
        "Community Councillor Results, Local Elections 2022",
        "https://www.carmarthenshire.gov.wales/council-democracy/elections-voting/local-elections-2022/community-councillor-results/",
        "carmarthenshire",
    ),
    "W06000003": (
        "Conwy County Borough Council",
        "Election results by community area, Local Government Elections, 5 May 2022",
        "https://modgovpub-eng.secure.conwy.gov.uk/mgElectionElectionAreaResults.aspx?Page=all&EID=104&RPID=20212970",
        "moderngov",
    ),
    "W06000021": (
        "Monmouthshire County Council",
        "Election results by community area, Community Council Elections, 5 May 2022",
        "https://democracy.monmouthshire.gov.uk/mgElectionElectionAreaResults.aspx?EID=11",
        "moderngov",
    ),
    "W06000002": (
        "Gwynedd Council",
        "Canlyniad Etholiad Diwrthwynebiad / Result of Uncontested Election, community and town councillors, 5 May 2022",
        "https://www.gwynedd.gov.uk/cy/Cyngor/Dogfennau-Cyngor/Pleidleisio-ac-etholiadau/Etholiad-Tref-Cymuned-2022/Uncontested-Cymuned.pdf",
        "gwynedd",
    ),
    "W06000005": (
        "Flintshire County Council",
        "Notice of Uncontested Election, town and community councils, 5 May 2022",
        "https://www.flintshire.gov.uk/en/PDFFiles/Council-Democracy/Elections-22/Notice-of-Uncontested-Election-TCC.pdf",
        "flintshire",
    ),
    "W06000014": (
        "Vale of Glamorgan Council",
        "Result of Uncontested Election, town and community councillors, 5 May 2022",
        "https://www.valeofglamorgan.gov.uk/en/our_council/Council-Structure/Elections/Local-Government-Election.aspx",
        "vale",
    ),
    "W06000008": (
        "Ceredigion County Council",
        "Town and Community Council Notices and Results, Local Elections 2022",
        "https://www.ceredigion.llyw.cymru/your-council/voting-elections/election-results/local-elections-2022/town-and-community-council-notices-and-results/",
        "ceredigion",
    ),
    "W06000013": (
        "Bridgend County Borough Council",
        "Election results: Town and Community Election results, 2022",
        "https://www.bridgend.gov.uk/council/elections/election-results",
        "bridgend",
    ),
    "W06000004": (
        "Denbighshire County Council",
        "Election results by community area, City, Town and Community Council Elections, 5 May 2022",
        "https://moderngov.denbighshire.gov.uk/mgElectionElectionAreaResults.aspx?Page=all&EID=93",
        "moderngov",
    ),
    "W06000018": (
        "Caerphilly County Borough Council",
        "Notice of Uncontested Elections, community councils, 5 May 2022",
        "https://www.caerffili.gov.uk/CaerphillyDocs/Elections/voting-2022/notice-of-uncontested-elections-communities-only.aspx",
        "caerphilly",
    ),
}
VALE = (
    "https://www.valeofglamorgan.gov.uk/Documents/Our%20Council/Council/Elections/Election%20Documents/Nominees/"
    "{}-Uncontested-Notices.pdf"
)
VALE_STEMS = [
    "Barry",
    "Colwinston",
    "Cowbridge",
    "Dinas-Powys",
    "Ewenny",
    "Llanmaes",
    "Llancarfan",
    "Llandough",
    "Llandow",
    "Llanfair",
    "Llangan",
    "Llantwit-Major",
    "Michaelston-le-Pit",
    "Penarth",
    "Pendoylan",
    "Penllyn",
    "Peterston-super-Ely",
    "St-Athan",
    "St-Brides-Major",
    "St-Donats",
    "St-Georges-super-Ely",
    "St-Nicholas-and-Bonvilston",
    "Sully",
    "Welsh-St-Donats",
    "Wenvoe",
    "Wick",
]
GWYNEDD_POLLS = (
    "https://www.gwynedd.llyw.cymru/cy/Cyngor/Dogfennau-Cyngor/Pleidleisio-ac-etholiadau/Etholiad-Tref-Cymuned-2022/"
    "Canlyniadau-Etholiad-2022.pdf"
)
GWYNEDD_NOMINATED = (
    "https://www.gwynedd.llyw.cymru/cy/Cyngor/Dogfennau-Cyngor/Pleidleisio-ac-etholiadau/Etholiad-Tref-Cymuned-2022/"
    "Statnom-Cymuned.pdf"
)
GWYNEDD_WARDS = {
    "Gwalia": "Llangywer",
    "Aberangell": "Mawddwy",
    "Dwyrain/East (Tywyn)": "Tywyn",
    "Morfa": "Tywyn",
    "Gorllewin/West (Tywyn)": "Tywyn",
    "Tanygrisiau": "Ffestiniog",
    "Hendre (Caernarfon)": "Caernarfon",
}
NOMINATED = (
    "Gwynedd Council's notice of uncontested election does not list this ward and no poll was held in it (the "
    "notice of poll covers eight other wards), so the candidates are taken from the statement of persons nominated, "
    "where they stand validly nominated for no more than the ward's seats, and are recorded as returned unopposed."
)
SCANS = {
    GWYNEDD_NOMINATED: (
        "Gwynedd Council",
        "Datganiad am y Personau a Enwebwyd / Statement of Persons Nominated, community and town councillors, "
        "5 May 2022",
    ),
    GWYNEDD_POLLS: (
        "Gwynedd Council",
        "Datganiad Canlyniad y Pleidleisio / Declaration of Result of Poll, community and town councillors, 5 May 2022",
    ),
}
SCANS |= {
    VALE.format(stem): (
        "Vale of Glamorgan Council",
        f"Result of Uncontested Election, {stem.replace('-', ' ')}, 5 May 2022",
    )
    for stem in VALE_STEMS
}
POLLS = Path(__file__).with_name("gwynedd_2022_polls.tsv")
VOTE_COLUMN = (0.685, 0.955)
ALIASES = {
    ("W06000018", "Llanbradach"): "Llanbradach and Pwllypant",
    ("W06000018", "Draethen, Waterloo and Rudry"): "Rudry",
    ("W06000018", "Risca Town"): "Risca West",
    ("W06000010", "Cwarter Bach"): "Quarter Bach",
    ("W06000010", "Dyffryn Cennen"): "Cyngor Bro Dyffryn Cennen",
    ("W06000010", "Llanddowror and Llanmiloe"): "Llanddowror",
    ("W06000010", "Llansteffan & Llanybri"): "Llansteffan",
    ("W06000003", "Bay of Colwyn"): "Colwyn Bay",
    ("W06000003", "Towyn and Kinmel Bay"): "Kinmel Bay and Towyn",
    ("W06000021", "Llanfoist"): "Llanfoist Fawr",
    ("W06000002", "LLANAELHAEARN"): "Trefor a Llanaelhaearn",
    ("W06000002", "LLANYMSTUMDWY"): "Llanystumdwy",
    ("W06000002", "Abermaw"): "Barmouth",
    ("W06000002", "Llangelynin"): "Llangelynnin",
    ("W06000005", "Trelawnyd & Gwaenysgor"): "Trelawnyd and Gwaenysgor",
    ("W06000005", "Gwernaffield and Pantymwyn"): "Gwernaffield with Pantymwyn",
    ("W06000014", "Llanmaes"): "Llan-maes",
    ("W06000014", "St Athan"): "St. Athan",
    ("W06000014", "St Brides Major"): "St. Bride's Major",
    ("W06000014", "St Donats"): "St. Donats",
    ("W06000014", "Welsh St Donats"): "Welsh St. Donats",
    ("W06000008", "Llanrhystud"): "Llanrhystyd",
    ("W06000008", "Llancynfelin"): "Llangynfelyn",
    ("W06000008", "Llanllwchaearn"): "Llanllwchaiarn",
    ("W06000008", "Ferwig"): "Y Ferwig",
    ("W06000013", "St. Brides Minor"): "St. Bride's Minor",
}
UNREGISTERED = {("W06000010", "Trelech a'r Betws"), ("W06000003", "Betws-y-Coed"), ("W06000021", "Llangybi Fawr")}
WARD_NAMES = {
    "Abersychan and Cwmavon": "Abersychan & Cwmavon",
    "Grifithstown East": "Griffithstown East",
    "Grifithstown West": "Griffithstown West",
}
PARTIES: dict[str, str | None] = {
    "Labour": "Q9630",
    "Conservative": "Q9626",
    "Welsh Conservatives": "Q9626",
    "Heritage Party": "Q105972889",
    "": None,
    "Independent": "independent",
    "Welsh Labour": "Q9630",
    "Welsh Labour/Llafur Cymru": "Q9630",
    "Welsh Conservative Party Candidate": "Q9626",
    "Plaid Cymru – The Party of Wales": "Q10691",
    "Welsh Labour / Llafur Cymru": "Q9630",
    "Independent / Annibynnol": "independent",
    "Welsh Liberal Democrats / Democratiaid Rhyddfrydol Cymru": "Q9624",
    "Reform UK": "Q104721252",
    "Gwlad - Wales Can be Better": "Q75321093",
    "Ceidwadwyr Cymreig / Welsh Conservatives": "Q9626",
    "Annibynnol": "independent",
    "Annibynnol / Independent": "independent",
    "Plaid Cymru - The Party of Wales": "Q10691",
    "Welsh Conservative Party Candidate / Ymgeisydd Plaid Geidwadol Cymru": "Q9626",
    "Labour and Co-operative Party / Llafur a'r Blaid Gydweithredol": "Q9630",
    "Wales Green Party/Plaid Werdd Cymru": "Q3499324",
    "Welsh Conservative Party": "Q9626",
    "Liberal Democrats": "Q9624",
    "Welsh Liberal Democrats": "Q9624",
    "Welsh Labour/Llafur Cymr": "Q9630",
    "Annibynnol/Independent": "independent",
    "Annibynnol/ Independent": "independent",
    "Annibynnol - Independent": "independent",
    "Plaid Cymru-The Party of Wales": "Q10691",
    "Plaid Cymru- The Party of Wales": "Q10691",
    "Welsh Liberal Democrats - Democratiaid Rhyddfrydol Cymru": "Q9624",
    "Welsh Liberal Democrats- Democratiaid Rhyddfrydol Cymru": "Q9624",
    "Welsh Conservative Party Candidate/ Ymgeisydd Plaid Geidwadol Cymru": "Q9626",
    "Llafur Cymru / Welsh Labour": "Q9630",
    "Welsh Labour Party/Llafur Cymru": "Q9630",
    "Welsh Labour – Llafur Cymru": "Q9630",
    "Independent Candidate": "independent",
    "Plaid Cymru": "Q10691",
    "Gwlad": "Q75321093",
    "Green Party/Plaid Werdd": "Q3499324",
    "The Green Party / Plaid Werdd": "Q3499324",
    "Labour and Co-operative Party / Llafur a’r Blaid Gydweithredol": "Q9630",
    "Independant": "independent",
    "Labour and Co-operative Party": "Q9630",
}


def normal(description: str) -> str:
    folded = description.casefold().replace("–", "-").replace("’", "'")
    return " ".join(re.sub(r"\s*([/-])\s*", r" \1 ", folded).split())


DESCRIPTIONS = {normal(key): key for key in PARTIES}
BY_CONTEXT = (
    "A by-election to fill a casual vacancy on a Welsh community or town council, held between the five-yearly "
    "elections, by block vote where a ward has more than one seat. Where only as many candidates are nominated as "
    "there are vacancies, those nominated are declared elected without a poll."
)
NO_WARD = (
    "Torfaen County Borough Council's page names no ward for this by-election, so the contest is recorded against "
    "the whole council area."
)
CAVEATS = {
    "Abergavenny (Cantref) - results": (
        "Monmouthshire County Council's page lists Bryony Nicholson three times: twice with 432 votes and no "
        "description, and once as Welsh Labour with no votes. She is stored once, with 432 votes. The page's total of "
        "2,827 votes counts her 432 twice; the six candidates' votes sum to 2,395."
    ),
}
GWYNEDD_CAVEATS = {
    ("Abermaw", "Abermaw"): (
        "Gwynedd Council's declaration gives Philip Hill's votes in a handwritten figure that reads as either 279 or "
        "379, so no count is stored for him; he is recorded as elected, as the declaration marks him."
    ),
    ("Llangelynin", "Llangelynin"): (
        "Gwynedd Council's declaration gives Hannah Elizabeth Johns's votes in an overwritten handwritten figure that "
        "cannot be read with certainty, so no count is stored for her."
    ),
    ("Mawddwy", "Llanymawddwy"): (
        'Gwynedd Council\'s declaration writes the total of rejected ballot papers as "25 (DP)" and does not explain '
        "the annotation."
    ),
}
FURTHER = (
    "Too few candidates were nominated for this ward by 5 April 2022 to fill its seats at the election of 5 May 2022, "
    "so a further election was called. Nominations closed at 4pm on {deadline}, and Bridgend County Borough Council's "
    "result of uncontested election is dated {dated}. The notice names no polling day, so the election carries the "
    "notice's date."
)
FURTHER_CONTEXT = (
    "A further election for the seats on a Welsh community or town council left unfilled at the ordinary election of "
    "5 May 2022. Where only as many candidates are nominated as there are vacancies, those nominated are declared "
    "elected without a poll."
)
BRIDGEND_WARDS: dict[str, str] = {}
CONTEXT = (
    "Wales's community and town councils are elected every five years on the day of the principal council "
    "elections, ward by ward where a community is warded, by block vote. Where no more candidates are nominated "
    "than there are seats, those nominated are declared elected without a poll."
)
SHORT = "{publisher}'s results list {elected} candidates declared elected unopposed to {places} seats, leaving {left} unfilled."
UNSTATED = (
    "{publisher}'s results list the candidates declared elected unopposed but not how many seats the ward has, "
    "so any seat left unfilled is not recorded."
)


def areas(url: str) -> list[str]:
    links = re.findall(r'href="(mgElectionAreaResults\.aspx[^"]+)"', raw.cached(SOURCE, url).path.read_text())
    return [urljoin(url, html.unescape(link)) for link in dict.fromkeys(links)]


def documents(url: str, reader: str) -> list[str]:
    page = raw.cached(SOURCE, url).path.read_text()
    if reader == "bridgend":
        start = page.index("Town and Community Election results")
        start = found(r">\s*2022\s*</button>\s*</h2>", page[start:]).end() + start
        page = page[start : page.index("<h2", start)]
    links = re.findall(r'href="([^"]+\.pdf)"', page)
    return [urljoin(url, html.unescape(link)) for link in dict.fromkeys(links)]


def fetch() -> None:
    for url in SCANS:
        try:
            if store(url):
                time.sleep(1)
        except httpx.HTTPStatusError as error:
            if error.response.status_code != 404 or "valeofglamorgan" not in url:
                raise
    for _, _, url, reader in COUNCILS.values():
        if reader == "vale":
            continue
        store(url)
        if reader == "moderngov":
            for page in areas(url):
                if store(page):
                    time.sleep(1)
        if reader in ("ceredigion", "bridgend"):
            for page in documents(url, reader):
                if store(page):
                    time.sleep(1)


def store(url: str) -> bool:
    try:
        raw.cached(SOURCE, url)
        return False
    except LookupError:
        pass
    response = httpx.get(url, headers={"User-Agent": BROWSER}, timeout=90, follow_redirects=True)
    response.raise_for_status()
    if response.content.startswith(b"%PDF"):
        suffix, media = ".pdf", "application/pdf"
    else:
        suffix, media = ".html", "text/html"
    raw.store(SOURCE, url, response.content, suffix=suffix, retrieved_at=datetime.now(UTC), media_type=media)
    return True


def text_of(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    if path.suffix == ".html":
        return path.read_text()
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def found(pattern: str, text: str, flags: int = 0) -> re.Match[str]:
    match = re.search(pattern, text, flags)
    if match is None:
        raise ValueError(f"{pattern} not found in {text[:200]}")
    return match


def flat(fragment: str) -> str:
    return " ".join(html.unescape(re.sub(r"<[^>]+>", " ", fragment)).split())


def torfaen(page: str) -> list[dict]:
    start = page.index("<h2>Community Council Elections 2022")
    section = page[start : page.index("<h2", start + 4)]
    reads: list[dict] = []
    council = ""
    for match in re.finditer(r"<(h3|h4)>(.*?)</\1>(.*?)(?=<h[34]>|$)", section, re.DOTALL):
        level, heading, body = match.group(1), flat(match.group(2)), match.group(3)
        uncontested = re.search(r"\s*-\s*Uncontested$", heading) is not None
        heading = re.sub(r"\s*-\s*Uncontested$", "", heading)
        if level == "h3":
            council = re.sub(r" (?:Town|Community) Council$", "", heading)
            if "<table" not in body:
                continue
            ward = None
        else:
            ward = WARD_NAMES.get(heading, heading)
        rows = [
            [flat(cell) for cell in re.findall(r"<td>(.*?)</td>", row, re.DOTALL)]
            for row in re.findall(r"<tr>(.*?)</tr>", body, re.DOTALL)
        ]
        rows = [row for row in rows if row]
        candidates: list[dict] = []
        for cells in rows:
            if uncontested:
                printed, _, description = cells
                candidates.append(
                    {
                        "printed": printed,
                        "name": proper(printed),
                        "description": description,
                        "votes": None,
                        "elected": True,
                    }
                )
            else:
                printed, description, count = cells
                votes = re.fullmatch(r"(\d+)( \(ELECTED\))?", count)
                if votes is None:
                    raise ValueError(f"Torfaen {council} {ward}: {count}")
                candidates.append(
                    {
                        "printed": printed,
                        "name": surname_first(printed),
                        "description": description,
                        "votes": int(votes.group(1)),
                        "elected": votes.group(2) is not None,
                    }
                )
        turnout = re.search(r"Turnout - (\d+)%", body)
        reads.append(
            {
                "council": council,
                "ward": ward,
                "places": None if uncontested else sum(1 for candidate in candidates if candidate["elected"]),
                "turnout": turnout.group(1) if turnout else None,
                "candidates": candidates,
            }
        )
    return reads


def torfaen_by_elections(page: str) -> list[dict]:
    reads: list[dict] = []
    end = page.index("<h2>Community Council Elections 2022")
    sections = re.findall(r"<h2>([^<]*?\d{4})</h2>(.*?)(?=<h2|$)", page[:end], re.DOTALL)
    for title, body in sections:
        title = html.unescape(title)
        council, when = found(r"^(.+?) (?:Town|Community) Councils? Elections? - (.+)$", title).groups()
        day = datetime.strptime(when, "%d %B %Y").date()
        for heading, part in re.findall(r"(?:<h3>(.*?)</h3>)?(.*?)(?=<h3>|$)", body, re.DOTALL):
            if "<table" not in part:
                continue
            ward = re.sub(r"\s*-\s*Uncontested$", "", flat(heading)) if heading else None
            uncontested = heading != "" and flat(heading).endswith("Uncontested")
            ward = re.sub(r"\s+Ward$", "", ward) if ward else None
            ward = WARD_NAMES.get(ward, ward)
            candidates: list[dict] = []
            for row in re.findall(r"<tr>(.*?)</tr>", part, re.DOTALL):
                cells = [flat(cell) for cell in re.findall(r"<td>(.*?)</td>", row, re.DOTALL)]
                if not cells:
                    continue
                printed = cells[0]
                if uncontested:
                    candidates.append(
                        {
                            "printed": printed,
                            "name": proper(printed),
                            "description": cells[2],
                            "votes": None,
                            "elected": True,
                        }
                    )
                    continue
                votes = re.fullmatch(r"(\d+)( \(ELECTED\))?", cells[2])
                if votes is None:
                    raise ValueError(f"Torfaen {title}: {cells[2]}")
                candidates.append(
                    {
                        "printed": printed,
                        "name": surname_first(printed),
                        "description": cells[1],
                        "votes": int(votes.group(1)),
                        "elected": votes.group(2) is not None,
                    }
                )
            reads.append(
                {
                    "council": re.sub(r"\s+", " ", council),
                    "ward": ward,
                    "day": day,
                    "by": True,
                    "caveat": None if ward else NO_WARD,
                    "places": None if uncontested else sum(1 for candidate in candidates if candidate["elected"]),
                    "turnout": None,
                    "candidates": candidates,
                }
            )
    return reads


def carmarthenshire(page: str) -> list[dict]:
    reads = []
    for panel in page.split('<div class="panel panel-default">')[1:]:
        title = flat(found(r'<h2 class="panel-title">(.*?)</h2>', panel, re.DOTALL).group(1))
        council, ward = found(r"^(.+?)(?: \((.+?)(?: Ward)?\))?$", title).groups()
        summary = flat(panel[panel.index("<h2>Summary") :].split("<h2>Downloads")[0])
        places = int(found(r"Number of Councillors to be elected : (\d+)", summary).group(1))
        uncontested = "This ward is uncontested" in summary
        table = found(r"<tbody>(.*?)</tbody>", panel, re.DOTALL).group(1)
        rows = [
            [flat(cell) for cell in re.findall(r"<td>(.*?)</td>", row, re.DOTALL)]
            for row in re.findall(r"<tr>(.*?)</tr>", table, re.DOTALL)
        ]
        elected = set()
        if not uncontested:
            block = panel[panel.index("<strong>Elected:</strong>") :].split("</p>")[0]
            elected = {flat(entry).split("/")[0].strip() for entry in block.split("<br />")[1:] if flat(entry)}
        candidates: list[dict] = []
        for cells in rows:
            if not cells[0]:
                continue
            printed, description = cells[0], cells[1]
            candidates.append(
                {
                    "printed": printed,
                    "name": surname_first(printed),
                    "description": description,
                    "votes": None if uncontested else int(cells[2]),
                    "elected": uncontested or printed in elected,
                }
            )
        if not uncontested:
            total = int(found(r"Total votes: (\d+)", summary).group(1))
            if total != sum(int(candidate["votes"]) for candidate in candidates) or len(elected) != places:
                raise ValueError(f"Carmarthenshire {title}: totals or elected do not agree")
            if elected - {candidate["printed"] for candidate in candidates}:
                raise ValueError(f"Carmarthenshire {title}: elected not among candidates")
        turnout = re.search(r"Turnout : ([\d.]+)%", summary)
        reads.append(
            {
                "council": council,
                "ward": ward,
                "places": places,
                "turnout": turnout.group(1) if turnout else None,
                "candidates": candidates,
            }
        )
    return reads


def moderngov(url: str) -> list[dict]:
    reads = []
    for page_url in areas(url):
        page = re.sub(r"<script\b.*?</script>", " ", text_of(page_url), flags=re.DOTALL)
        if "No election candidates have been specified for this area" in page:
            continue
        caption = flat(found(r'<caption\s+class="mgSectionTitle"\s*>(.*?)</caption>', page, re.DOTALL).group(1))
        english = list(re.finditer(r"/\s*([^/(]+?) (?:Town|Community|City) Counci?l?\b", caption))
        council = english[-1].group(1) if english else found(r"^(.+?)(?: \(| - results$)", caption).group(1)
        ward = re.search(r"\(([^()]+?)\)\s*- results$", caption[english[-1].end() if english else 0 :])
        ward = re.sub(r"\s+[Ww]ard$", "", ward.group(1).strip()) if ward else None
        bracketed = re.match(r"^(.+?) \[(.+?)\] - results$", caption)
        if bracketed:
            council, ward = bracketed.groups()
        table = page[page.index('class="mgStatsTable') :].split("</table>")[0]
        candidates: list[dict] = []
        rows = [
            [flat(cell) for cell in re.findall(r"<td[^>]*>(.*?)</td>", row, re.DOTALL)]
            for row in re.findall(r"<tr\s+class=\"mgTable(?:Odd|Even)Row\"\s*>(.*?)</tr>", table, re.DOTALL)
        ]
        voted = any(row[2] not in ("No contest", "") for row in rows)
        described = {row[0]: row[1] for row in rows if row[1]}
        seen = set()
        for printed, _, count, _, outcome in rows:
            if printed in seen or (voted and count == ""):
                continue
            seen.add(printed)
            candidates.append(
                {
                    "printed": printed,
                    "name": printed,
                    "description": described.get(printed, ""),
                    "votes": None if count in ("No contest", "") else int(count.replace(",", "")),
                    "elected": outcome == "Elected" or (count == "" and outcome == ""),
                }
            )
        summary = flat(page[page.index("Voting Summary") :].split("</table>")[0]) if "Voting Summary" in page else ""

        def figure(label: str) -> int | None:
            found = re.search(rf"{label} ([\d,]+)", summary)
            return int(found.group(1).replace(",", "")) if found else None

        places = figure("Seats")
        if candidates[0]["votes"] is not None:
            if caption not in CAVEATS and figure("Total votes") != sum(int(c["votes"]) for c in candidates):
                raise ValueError(f"{caption}: total votes do not agree")
            if places != sum(1 for candidate in candidates if candidate["elected"]):
                raise ValueError(f"{caption}: seats and elected do not agree")
        turnout = re.search(r"Turnout ([\d.]+)%", summary)
        reads.append(
            {
                "council": council,
                "ward": ward,
                "places": places,
                "turnout": turnout.group(1) if turnout else None,
                "electorate": figure("Electorate"),
                "counted": figure("Number of ballot papers included in the count"),
                "issued": figure("Number of ballot papers issued"),
                "rejected": figure("Number of ballot papers rejected"),
                "url": page_url,
                "caveat": CAVEATS.get(caption),
                "candidates": candidates,
            }
        )
    return reads


def communities(lad: str) -> dict[str, str]:
    with connect() as conn:
        rows = conn.execute(
            """select en.text, n.text from external_id be join body b on b.id = be.entity_id
               join polity_parent pp on pp.polity_id = b.polity_id
               join external_id lp on lp.entity_id = pp.parent_id and lp.scheme = 'gb.polity'
               join name en on en.entity_id = b.polity_id and en.is_preferred and en.lang = 'en'
               join name n on n.entity_id = b.polity_id and n.lang in ('en', 'cy')
               where be.scheme = 'gb.body' and lp.value = %s and b.kind = 'council'""",
            [lad],
        ).fetchall()
    names = {fold(other): english for english, other in rows}
    for (alias_lad, printed), english in ALIASES.items():
        if alias_lad == lad:
            names[fold(printed)] = english
    return names


def welsh_title(text: str) -> str:
    words = re.split(r"([ /()-])", re.sub(r"(\S)\(", r"\1 (", " ".join(text.split())).lower())
    out: list[str] = []
    for word in words:
        if word.strip(" /()-"):
            out.append(word if out and word in ("y", "a", "ap") else word[:1].upper() + word[1:])
        else:
            out.append(word)
    return "".join(out)


def gwynedd(url: str) -> list[dict]:
    names = communities("W06000002")
    reads: list[dict] = []
    for notice in text_of(url).split("ETHOL CYNGHORYDD/CYNGHORWYR")[1:]:
        heading = " ".join(found(r"FOR THE\s*\n(.*?)\n\s*\d\d/\d\d/\d{4}", notice, re.DOTALL).group(1).split())
        words = found(r"CYMUNED/TREF [GC]YNGOR (?:CYMUNED|TREF|DINAS) (.+)", heading.replace("COMMUNITY/TOWN", ""))
        words = words.group(1).split()
        cut = next((cut for cut in range(len(words), 0, -1) if fold(" ".join(words[:cut])) in names), 0)
        if not cut:
            raise ValueError(f"Gwynedd: no council in {heading}")
        block = found(r"Home Address\s*\n(.*?)RHODDWYD", notice, re.DOTALL).group(1)
        printed = [re.split(r"\s{3,}", line.strip())[0] for line in block.splitlines() if re.match(r" {0,4}\S", line)]
        if not printed:
            continue
        reads.append(
            {
                "council": names[fold(" ".join(words[:cut]))],
                "ward": welsh_title(" ".join(words[cut:])) or None,
                "places": None,
                "turnout": None,
                "candidates": [
                    {
                        "printed": line,
                        "name": " ".join([*line.split()[:-1], line.split()[-1][:1].upper() + line.split()[-1][1:]]),
                        "description": "",
                        "votes": None,
                        "elected": True,
                    }
                    for line in printed
                ],
            }
        )
    return reads


def gwynedd_polls() -> list[dict]:
    names = communities("W06000002")
    frame = pl.read_csv(POLLS, separator="\t", infer_schema_length=0)
    reads: list[dict] = []
    for (council, ward), lines in frame.group_by("council", "ward", maintain_order=True):
        assert isinstance(council, str) and isinstance(ward, str)
        rejected = lines.filter(pl.col("printed").is_null()).rows(named=True)
        candidates = [
            {
                "printed": line["printed"],
                "name": line["name"] or display(line["printed"]),
                "description": line["description"] or "",
                "votes": int(line["votes"]) if line["votes"] else None,
                "elected": line["elected"] == "1",
                "box_page": int(line["page"]),
                "box": [VOTE_COLUMN[0], float(line["top"]), VOTE_COLUMN[1], float(line["bottom"])],
            }
            for line in lines.filter(pl.col("printed").is_not_null()).iter_rows(named=True)
        ]
        if any(candidate["votes"] is None for candidate in candidates) and (council, ward) not in GWYNEDD_CAVEATS:
            raise ValueError(f"Gwynedd {council} {ward}: a count is missing without a caveat")
        box = (
            [VOTE_COLUMN[0], float(rejected[0]["top"]), VOTE_COLUMN[1], float(rejected[0]["bottom"])]
            if rejected
            else None
        )
        reads.append(
            {
                "council": names[fold(council)],
                "ward": ward,
                "places": sum(1 for candidate in candidates if candidate["elected"]),
                "turnout": None,
                "source": GWYNEDD_POLLS,
                "url": GWYNEDD_POLLS,
                "rejected": int(rejected[0]["votes"]) if rejected else None,
                "rejected_page": int(rejected[0]["page"]) if rejected else None,
                "rejected_box": box,
                "caveat": GWYNEDD_CAVEATS.get((council, ward)),
                "candidates": candidates,
            }
        )
    return reads


def gwynedd_nominated() -> list[dict]:
    path = raw.cached(SOURCE, GWYNEDD_NOMINATED).path
    out = subprocess.run(["pdftotext", "-tsv", str(path), "-"], capture_output=True, text=True, check=True).stdout
    pages: dict[int, list[dict]] = {}
    for word in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        if word["level"] == "5":
            pages.setdefault(int(word["page_num"]), []).append(word)
    reads: list[dict] = []
    for number, words in sorted(pages.items()):
        text = " ".join(word["text"] for word in words)
        top = max(float(word["top"]) for word in words if word["text"] == "blaenorol)") + 10
        bottom = min(float(word["top"]) for word in words if word["text"] == "Mae’r")
        body = [word for word in words if top < float(word["top"]) < bottom]
        lines: dict[int, list[str]] = {}
        for word in body:
            if float(word["left"]) < 145:
                lines.setdefault(round(float(word["top"])), []).append(word["text"])
        if not lines:
            continue
        rows = sorted(lines.items())
        pairs = [(rows[k], rows[k + 1]) for k in range(0, len(rows) - 1, 2)] if len(rows) % 2 == 0 else None
        described: dict[int, list[str]] = {}
        for word in body:
            left = float(word["left"])
            if pairs and 300 < left < 495:
                middle = float(word["top"])
                near = min(range(len(pairs)), key=lambda i: abs((pairs[i][0][0] + pairs[i][1][0]) / 2 - middle))
                described.setdefault(near, []).append(word["text"])
            elif left > 715:
                raise ValueError(f"Gwynedd statement page {number}: a nomination is marked invalid or withdrawn")
        reads.append(
            {
                "ward": welsh_title(found(r"ELECTORAL WARD - (.*?) Dydd", text).group(1)),
                "seats": int(found(r"Election of (\d+) Community", text).group(1)),
                "page": number,
                "candidates": None
                if pairs is None
                else [
                    {
                        "printed": f"{' '.join(surname[1])} {' '.join(forenames[1])}",
                        "name": f"{' '.join(forenames[1])} {' '.join(surname[1])}",
                        "description": " ".join(described.get(index, [])),
                        "votes": None,
                        "elected": True,
                    }
                    for index, (surname, forenames) in enumerate(pairs)
                ],
            }
        )
    return reads


def gwynedd_seats(reads: list[dict]) -> list[dict]:
    names = communities("W06000002")
    by_ward = {fold(read["ward"]): read["council"] for read in reads if read["ward"]}
    entries = gwynedd_nominated()
    for entry in entries:
        ward = entry["ward"]
        entry["council"] = by_ward.get(fold(ward)) or names.get(fold(ward)) or GWYNEDD_WARDS[ward]
    for entry in entries:
        ward, council = entry["ward"], entry["council"]
        single = sum(1 for other in entries if other["council"] == council) == 1
        matches = [
            read
            for read in reads
            if read["council"] == council and (read["ward"] is None or fold(read["ward"]) == fold(ward) or single)
        ]
        if matches:
            for read in matches:
                elected = sum(1 for candidate in read["candidates"] if candidate["elected"])
                if elected > entry["seats"] or (read["places"] is not None and read["places"] != entry["seats"]):
                    raise ValueError(f"Gwynedd {council} {ward}: {elected} elected to {entry['seats']} seats")
                read["places"] = entry["seats"]
            continue
        candidates = entry["candidates"]
        if candidates is None or len(candidates) > entry["seats"]:
            raise ValueError(f"Gwynedd {council} {ward}: no unopposed return can be read")
        reads.append(
            {
                "council": council,
                "ward": ward,
                "places": entry["seats"],
                "turnout": None,
                "source": GWYNEDD_NOMINATED,
                "url": GWYNEDD_NOMINATED,
                "caveat": NOMINATED,
                "candidates": candidates,
            }
        )
    if missing := [(read["council"], read["ward"]) for read in reads if read["places"] is None]:
        raise ValueError(f"Gwynedd: no seat count for {missing}")
    return reads


def flintshire(url: str) -> list[dict]:
    names = communities("W06000005")
    reads: list[dict] = []
    for notice in text_of(url).split("Notice of Uncontested Election")[1:]:
        heading = " ".join(found(r"^(.*?)Date of Election", notice, re.DOTALL).group(1).split()).replace("`", "'")
        match = found(r"^(.+?) (?:Community|Town) Council(?: \((.+?)(?: Ward)?\))?$", heading)
        council, ward = match.group(1), match.group(2)
        lines = (
            found(r"address is situated\n(.*?)\n[^\n]*\n\s*Returning Officer", notice, re.DOTALL).group(1).splitlines()
        )
        column = (
            found(r"Description", notice).start() - notice.rfind("\n", 0, found(r"Description", notice).start()) - 1
        )
        starts = [number for number, line in enumerate(lines) if line[:1].strip()]
        if len(starts) % 2:
            raise ValueError(f"Flintshire {heading}: odd name lines")
        candidates = []
        for first, last in zip(starts[::2], [*starts[2::2], len(lines)], strict=True):
            surname = re.split(r"\s{3,}", lines[first].strip())[0].replace("`", "'")
            forenames = re.split(r"\s{3,}", lines[first + 1].strip())[0]
            description = " ".join(
                part for part in (line[column:].strip() for line in lines[first:last] if len(line) > column) if part
            )
            candidates.append(
                {
                    "printed": f"{surname} {forenames}",
                    "name": surname_first(f"{surname}, {forenames}"),
                    "description": description,
                    "votes": None,
                    "elected": True,
                }
            )
        if not candidates:
            raise ValueError(f"Flintshire {heading}: no candidates")
        reads.append(
            {
                "council": names[fold(ALIASES.get(("W06000005", council), council))],
                "ward": ward,
                "places": None,
                "turnout": None,
                "candidates": candidates,
            }
        )
    return unwarded(reads, "W06000005")


def vale(url: str) -> list[dict]:
    names = communities("W06000014")
    reads: list[dict] = []
    for stem in VALE_STEMS:
        page = VALE.format(stem)
        try:
            text = text_of(page)
        except LookupError:
            continue
        heading = " ".join(found(r"CYMUNED\n(.*?)VALE OF GLAMORGAN", text, re.DOTALL).group(1).split())
        match = found(r"^(.+?) (?:COMMUNITY|TOWN) COUNCIL - (.+?)(?: WARD)?$", heading)
        council = welsh_title(match.group(1))
        ward = welsh_title(match.group(2))
        block = found(r"ETHOLWYD\n(.*?)\n\s*Dated", text, re.DOTALL).group(1)
        candidates = []
        for line in block.split("\n"):
            words = line.split()
            if not words:
                continue
            cut = next((cut for cut, word in enumerate(words) if not word.isupper()), 0)
            if not cut:
                raise ValueError(f"Vale {stem}: cannot split {line}")
            candidates.append(
                {
                    "printed": " ".join(words),
                    "name": surname_first(f"{' '.join(words[:cut])}, {' '.join(words[cut:])}"),
                    "description": "",
                    "votes": None,
                    "elected": True,
                }
            )
        reads.append(
            {
                "council": names[fold(ALIASES.get(("W06000014", council), council))],
                "ward": ward,
                "places": None,
                "turnout": None,
                "source": page,
                "url": page,
                "candidates": candidates,
            }
        )
    return unwarded(reads, "W06000014")


def caerphilly(url: str) -> list[dict]:
    names = communities("W06000018")
    reads: list[dict] = []
    parts = text_of(url).split("NOTICE OF UNCONTESTED ELECTION")
    for before, notice in zip(parts, parts[1:], strict=False):
        heading = [line.strip() for line in before.splitlines() if line.strip()][-1]
        council = None
        for piece in reversed([part.strip() for part in heading.split("/")]):
            if fold(piece.replace("&", "and")) in names:
                council = names[fold(piece.replace("&", "and"))]
                break
        if council is None:
            raise ValueError(f"Caerphilly: no council in {heading}")
        ward = " ".join(found(r"Name of Community Ward:\s*(.*?)\s*Enw", notice, re.DOTALL).group(1).split())
        ward = ward.split(" / ")[-1].strip()
        places = int(found(r"election of (\d+) Community Councillors", notice).group(1))
        block = found(r"cartref.\n(.*?)\n.If a Candidate", notice, re.DOTALL).group(1)
        lines = [line[:60].rstrip() for line in block.splitlines() if line[:60].strip()]
        candidates = []
        for line in lines:
            if re.match(r"^ ?[^\s]", line) and re.match(r"^\s*[A-Z][A-Z' .-]+, ", line):
                candidates.append({"printed": line.strip(), "description": ""})
            elif candidates and not candidates[-1]["description"]:
                candidates[-1]["description"] = line.strip().split(" / ")[0]
        if not candidates or len(candidates) > places:
            raise ValueError(f"Caerphilly {council} {ward}: {len(candidates)} candidates for {places} places")
        reads.append(
            {
                "council": council,
                "ward": ward,
                "places": places,
                "turnout": None,
                "candidates": [
                    {
                        "printed": c["printed"],
                        "name": surname_first(c["printed"]),
                        "description": c["description"],
                        "votes": None,
                        "elected": True,
                    }
                    for c in candidates
                ],
            }
        )
    return unwarded(reads, "W06000018")


def unwarded(reads: list[dict], lad: str) -> list[dict]:
    names = communities(lad)
    counts: dict[str, int] = {}
    for read in reads:
        counts[read["council"]] = counts.get(read["council"], 0) + 1
    for read in reads:
        if read["ward"] and names.get(fold(read["ward"])) == read["council"] and counts[read["council"]] == 1:
            read["ward"] = None
    return reads


NUMBERS = {
    word: number
    for number, word in enumerate(
        ["zero", "one", "two", "three", "four", "five", "six", "seven", "eight", "nine", "ten", "eleven", "twelve"]
    )
}


def chunks(line: str) -> list[tuple[float, str]]:
    return [(match.start() + len(match.group()) / 2, match.group()) for match in re.finditer(r"\S+(?: \S+)*", line)]


def caps(text: str) -> str:
    return display(text).replace("'R ", "'r ") if text.isupper() else text


def forenames_first(printed: str) -> str:
    surname, forenames = (part.strip() for part in printed.split(",", 1))
    return " ".join([*(display(word) if word.isupper() else word for word in forenames.split()), display(surname)])


def canonical(description: str) -> str:
    return DESCRIPTIONS.get(normal(description), description)


def place(text: str) -> tuple[str, str | None]:
    english = " ".join(text.split()).split(" / ")[-1]
    council, ward = found(r"^(.+?)(?: \((.+)\))?$", english).groups()
    return caps(council), caps(ward) if ward else None


def ceredigion(url: str) -> list[dict]:
    reads = []
    for document in documents(url, "ceredigion"):
        text = text_of(document)
        lines = text.splitlines()
        if "RESULT OF UNCONTESTED ELECTION" in text:
            start = next(i for i, line in enumerate(lines) if line.strip() == "Town/Community")
            end = next(i for i, line in enumerate(lines) if line.strip().startswith("Ethol"))
            council, ward = place(" ".join(lines[start + 1 : end]))
            places = int(found(r"^\s*Ethol\s+(\d+)\s", lines[end]).group(1))
            head = next(i for i, line in enumerate(lines) if "Candidate name" in line)
            candidates: list[dict] = []
            for line in lines[head + 1 :]:
                if "Dyddiedig" in line:
                    break
                parts = chunks(line)
                if not parts:
                    continue
                name = " ".join(part for center, part in parts if center < 90)
                description = " ".join(part for center, part in parts if center >= 90)
                if "," not in name:
                    raise ValueError(f"{document}: {line}")
                candidates.append(
                    {
                        "printed": name,
                        "name": forenames_first(name),
                        "description": canonical(description),
                        "votes": None,
                        "elected": True,
                    }
                )
            reads.append(
                {
                    "council": council,
                    "ward": ward,
                    "places": places,
                    "turnout": None,
                    "url": document,
                    "candidates": candidates,
                }
            )
            continue
        start = next(i for i, line in enumerate(lines) if "Cyngor Tref / Cymuned" in line)
        end = next(i for i, line in enumerate(lines) if line.strip() == "Town / Community Council")
        council, ward = place(" ".join(lines[start + 1 : end]))
        places = NUMBERS[found(r"Returning Officer at the Election of (\w+)", text).group(1).lower()]
        head = next(i for i, line in enumerate(lines) if "Surname" in line and "Other Names" in line)
        stop = next(i for i, line in enumerate(lines) if "Yr oedd nifer" in line)
        columns = [lines[head].index(label) + len(label) / 2 for label in ("Surname", "Other Names", "Description")]
        bounds = [(columns[0] + columns[1]) / 2, (columns[1] + columns[2]) / 2, columns[2] + 35]
        rows: list[list[list[str]]] = []
        for line in lines[head + 1 : stop]:
            parts = chunks(line)
            if not parts:
                continue
            if re.fullmatch(r"\d+", parts[-1][1]) and parts[-1][0] > bounds[2]:
                rows.append([[], [], [], [], []])
            if not rows:
                raise ValueError(f"{document}: {line}")
            for center, part in parts:
                column = sum(center > bound for bound in bounds)
                if column == 3 and not re.fullmatch(r"\d+", part):
                    column = 4
                rows[-1][column].append(part)
        candidates = []
        for surname, others, description, votes, flags in rows:
            surname_text, forenames = " ".join(surname), " ".join(others)
            printed = f"{surname_text}, {forenames}"
            plain_surname = re.sub(r"\s*\(.*?\)", "", surname_text)
            plain_forenames = re.sub(r"\s*\(.*?\)", "", forenames)
            candidates.append(
                {
                    "printed": printed,
                    "name": f"{plain_forenames} {display(plain_surname)}",
                    "description": canonical(" ".join(description)),
                    "votes": int(votes[0]),
                    "elected": "ETHOLWYD / ELECTED" in flags,
                }
            )
        if sum(1 for candidate in candidates if candidate["elected"]) != places:
            raise ValueError(f"{document}: seats and elected do not agree")
        reads.append(
            {
                "council": council,
                "ward": ward,
                "places": places,
                "turnout": None,
                "rejected": int(found(r"TOTAL\W+(\d+)", text).group(1)),
                "url": document,
                "candidates": candidates,
            }
        )
    return reads


def bridgend(url: str) -> list[dict]:
    reads = []
    empty = []
    for document in documents(url, "bridgend"):
        text = text_of(document)
        lines = text.splitlines()
        if "DECLARATION OF RESULT OF POLL" in text:
            area = found(r"^\s*(\S.*? (?:Town|Community) Council(?: - .+?)?)\s*$", text, re.MULTILINE).group(1)
            head = next(i for i, line in enumerate(lines) if "ENW / NAME" in line)
            split = lines[head].index("Disgrifiad") - 1
            candidates = []
            for line in lines[head + 1 :]:
                if line.startswith("Felly"):
                    break
                if not line.strip():
                    continue
                printed = line[:split].strip()
                description, elected, votes = found(r"^(.*?)\s*(\(E\)\s+)?([\d,]+)$", line[split:].strip()).groups()
                candidates.append(
                    {
                        "printed": printed,
                        "name": forenames_first(printed),
                        "description": canonical(description or ""),
                        "votes": int(votes.replace(",", "")),
                        "elected": elected is not None,
                    }
                )
            declared = found(
                r"Therefore, I give public notice that (.+?),? (?:is|are) duly elected", " ".join(text.split())
            )
            if len([n for n in declared.group(1).split(", ") if n]) != sum(1 for c in candidates if c["elected"]):
                raise ValueError(f"{document}: declared and elected do not agree: {declared.group(1)}")
            cast = int(
                found(r"The total number of votes cast across .+? was ([\d,]+)", " ".join(text.split()))
                .group(1)
                .replace(",", "")
            )
            rejected = int(found(r"The total number of ballot papers rejected was (\d+)", text).group(1))
            turnout = found(r"% Turnout: ([\d.]+)", text).group(1)
            day, by, note = DAY, False, None
            places = sum(1 for candidate in candidates if candidate["elected"])
            context = (
                f"Bridgend County Borough Council prints a total of {cast:,} votes cast across the ward, "
                f"{rejected:,} ballot papers rejected, and a turnout of {turnout}%."
            )
        else:
            joined = " ".join(text.split())
            start = next(i for i, line in enumerate(lines) if "For the Area of" in line)
            area = next(line.strip() for line in lines[start + 1 :] if line.strip())
            places = int(found(r"Election of (\d+) Councillor", text).group(1))
            deadline = datetime.strptime(
                found(r"namely 4pm on \w+,? (\d+)(?:st|nd|rd|th)? (\w+ \d{4})", joined).expand(r"\1 \2"), "%d %B %Y"
            ).date()
            dated = datetime.strptime(
                found(r"Dated / Dyddiedig: \w+,? (\d+ \w+ \d{4})", joined).group(1), "%d %B %Y"
            ).date()
            if "no person stood validly nominated" in joined:
                empty.append((area, deadline))
                continue
            head = next(i for i, line in enumerate(lines) if "HOME ADDRESS" in line)
            address = lines[head].index("HOME ADDRESS")
            column = lines[head].index("DESCRIPTION") - 3
            candidates = []
            for line in lines[head + 2 :]:
                if "Shephard" in line or "Returning Officer" in line:
                    break
                if not line.strip():
                    continue
                name = line[:address].strip()
                description = " ".join(part for center, part in chunks(line) if center - len(part) / 2 >= column)
                if name:
                    candidates.append(
                        {
                            "printed": name,
                            "name": forenames_first(name),
                            "description": description,
                            "votes": None,
                            "elected": True,
                        }
                    )
                elif description:
                    candidates[-1]["description"] = f"{candidates[-1]['description']} {description}".strip()
            for candidate in candidates:
                candidate["description"] = canonical(str(candidate["description"]))
            further = deadline.month == 5
            day, by = (dated, True) if further else (DAY, False)
            note = (
                FURTHER.format(deadline=f"{deadline.day} {deadline:%B %Y}", dated=f"{dated.day} {dated:%B %Y}")
                if further
                else None
            )
            context, rejected = None, None
        council, ward = found(r"^(.+?) (?:Town|Community) Council(?: [-–] (.+?))?$", " ".join(area.split())).groups()
        ward = re.sub(r"\s+Ward$", "", ward) if ward else None
        reads.append(
            {
                "council": council,
                "ward": BRIDGEND_WARDS.get(ward, ward),
                "day": day,
                "by": by,
                "places": places,
                "turnout": None,
                "rejected": rejected,
                "url": document,
                "caveat": note,
                "context": context,
                "election_context": FURTHER_CONTEXT if by else None,
                "candidates": candidates,
            }
        )
    for area, deadline in empty:
        print(f"Bridgend: no one nominated by {deadline}: {area}", file=sys.stderr)
    return reads


READERS = {
    "torfaen": lambda url: torfaen(text_of(url)) + torfaen_by_elections(text_of(url)),
    "carmarthenshire": lambda url: carmarthenshire(text_of(url)),
    "moderngov": moderngov,
    "flintshire": flintshire,
    "vale": vale,
    "gwynedd": lambda url: unwarded(gwynedd_seats(gwynedd(url) + gwynedd_polls()), "W06000002"),
    "ceredigion": ceredigion,
    "bridgend": bridgend,
    "caerphilly": caerphilly,
}


def parse() -> None:
    with connect() as conn:
        councils = conn.execute(
            """select lp.value, n.text, be.value, a.scheme_code, c.code
               from external_id be join body b on b.id = be.entity_id
               join polity_parent pp on pp.polity_id = b.polity_id
               join external_id lp on lp.entity_id = pp.parent_id and lp.scheme = 'gb.polity'
               join polity_territory pt on pt.polity_id = b.polity_id and upper_inf(pt.valid)
               join area a on a.id = pt.area_id join area_code c on c.area_id = a.id
               join name n on n.entity_id = b.polity_id and n.is_preferred and n.lang = 'en'
               where be.scheme = 'gb.body' and lp.value = any(%s) and b.kind = 'council'""",
            [sorted(COUNCILS)],
        ).fetchall()
    known = {(lad, fold(name)): (key, scheme, code) for lad, name, key, scheme, code in councils}
    rows = []
    unknown = set()
    for lad, (_, _, url, reader) in COUNCILS.items():
        for read in READERS[reader](url):
            found = known.get((lad, fold(ALIASES.get((lad, read["council"]), read["council"]))))
            if (lad, read["council"]) in UNREGISTERED:
                continue
            if found is None:
                unknown.add((lad, read["council"]))
                continue
            key, scheme, code = found
            for number, candidate in enumerate(read["candidates"], start=1):
                if candidate["description"] not in PARTIES:
                    raise ValueError(f"unknown description: {candidate['description']}")
                rows.append(
                    {
                        "lad": lad,
                        "url": read.get("source", url),
                        "council": read["council"],
                        "key": key,
                        "scheme": scheme,
                        "code": code,
                        "ward": read["ward"],
                        "day": read.get("day", DAY),
                        "by": read.get("by", False),
                        "places": read["places"],
                        "turnout": read["turnout"],
                        "electorate": read.get("electorate"),
                        "counted": read.get("counted"),
                        "issued": read.get("issued"),
                        "rejected": read.get("rejected"),
                        "page": read.get("url", url),
                        "rejected_page": read.get("rejected_page"),
                        "rejected_box": read.get("rejected_box"),
                        "caveat": read.get("caveat"),
                        "context": read.get("context"),
                        "election_context": read.get("election_context"),
                        "number": number,
                        **candidate,
                    }
                )
    if unknown:
        raise ValueError(f"no council in the register: {sorted(unknown)}")
    frame = pl.DataFrame(rows, infer_schema_length=None)
    if clash := frame.group_by("key", "ward", "day").agg(pl.col("url").n_unique()).filter(pl.col("url") > 1).rows():
        raise ValueError(f"two sources for one ward: {clash}")
    contests = frame.select("key", "ward", "day").unique().height
    uncontested = frame.group_by("key", "ward", "day").agg(pl.col("votes").is_null().all())["votes"].sum()
    print(
        f"{frame['key'].n_unique()} councils, {contests} contests ({uncontested} uncontested), "
        f"{frame.height} candidates",
        file=sys.stderr,
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Welsh community and town council elections of 5 May 2022 and later by-elections, ward by ward, from "
            "the principal councils' published results: polls with votes, and wards filled unopposed as tacit sets",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        systems = {code: cs.key("gb.electoral_system", code) for code in ("fptp", "block")}
        if any(system in cs.minted for system in systems.values()):
            raise ValueError("publish gb.parliament first")
        gb = cs.polity("GB")
        if conn.execute("select 1 from scheme where code = %s", [WARDS]).fetchone() is None:
            cs.add(
                "scheme", code=WARDS, polity_id=gb, kind="electoral_district", rank=5, noun="parish or community ward"
            )
        sources: dict[str, UUID] = {}
        artifacts: dict[str, UUID] = {}
        listed = [(publisher, title, url) for publisher, title, url, _ in COUNCILS.values()]
        for publisher, title, url in listed + [(publisher, title, url) for url, (publisher, title) in SCANS.items()]:
            if url not in set(frame["url"]):
                continue
            sources[url] = cs.source(publisher, title)
            cs.add("source", id=sources[url], kind="official_body", publisher=publisher, title=title, url=url)
            for page in frame.filter(pl.col("url") == url)["page"].unique().sort():
                artifacts[page] = cs.artifact(raw.cached(SOURCE, page), sources[url])

        @functools.cache
        def page_of(url: str, number: int) -> UUID:
            artifact = raw.cached(SOURCE, url)
            image = PAGES / f"{artifact.sha256}-{number}.png"
            if not image.exists():
                PAGES.mkdir(parents=True, exist_ok=True)
                subprocess.run(
                    ["pdftoppm", "-r", "150", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                    + [str(artifact.path), str(image.with_suffix(""))],
                    check=True,
                )
            width, height = struct.unpack(">II", image.read_bytes()[16:24])
            found = cs.page(artifacts[url], number)
            cs.add(
                "source_page",
                id=found,
                artifact_id=artifacts[url],
                page_number=number,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
            return found

        def locate(result_set: UUID, unit: UUID, option: UUID | None, measure: int, url: str, number: int, box: list):
            cs.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                page_id=page_of(url, number),
                source_id=None,
                x0=box[0],
                y0=box[1],
                x1=box[2],
                y1=box[3],
            )

        keys = sorted(set(frame["key"]))
        paths = dict(
            conn.execute(
                """select e.value, s.path from external_id e join slug s on s.entity_id = e.entity_id and s.is_current
                   where e.scheme = 'gb.polity' and e.value = any(%s)""",
                [keys],
            ).fetchall()
        )
        bodies = dict(
            conn.execute(
                """select e.value, n.text from external_id e join name n on n.entity_id = e.entity_id and n.is_preferred
                   where e.scheme = 'gb.body' and e.value = any(%s)""",
                [keys],
            ).fetchall()
        )
        parties: dict[str, UUID] = {}

        def party_of(description: str) -> UUID | None:
            target = PARTIES[description]
            if target is None or target == "independent":
                return None
            if target not in parties:
                parties[target] = cs.key("wikidata", target)
                if parties[target] in cs.minted:
                    raise ValueError(f"{target} is not a party in the archive")
            return parties[target]

        people: dict[str, UUID] = {}
        elections: set[str] = set()
        added: set[UUID] = set()
        for (key, ward, day), lines in frame.group_by("key", "ward", "day", maintain_order=True):
            assert isinstance(key, str)
            first = lines.row(0, named=True)
            by = first["by"]
            places = first["places"]
            tacit = lines["votes"].is_null().all()
            polity = cs.key("gb.polity", key)
            council_area = cs.area(first["scheme"], first["code"])
            election_key = f"{day}:wcc:{key}"
            election = cs.key("gb.election", election_key)
            election_path = f"{paths[key]}/{day}-local"
            if election_key not in elections:
                elections.add(election_key)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    kind="by_election" if by else "local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(
                    election,
                    f"{bodies[key]} by-election, {day.day} {day:%B %Y}" if by else f"{day.year} {bodies[key]} election",
                )
                cs.add("slug", path=election_path, entity_id=election)
                cs.add(
                    "note",
                    entity_id=election,
                    kind="context",
                    body_md=first["election_context"] or (BY_CONTEXT if by else CONTEXT),
                )
            if ward is None:
                scheme, code, area = first["scheme"], first["code"], council_area
            else:
                scheme, code = WARDS, f"{key}:{slug(ward)}"
                area = cs.area(WARDS, code)
                if area in cs.minted and area not in added:
                    added.add(area)
                    cs.add("area", id=area, scheme_code=WARDS)
                    cs.add("area_code", area_id=area, code=code, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=area, parent_id=council_area, valid_from=None, valid_to=None)
                    name(area, ward)
            unit = cs.unit(scheme, code)
            if unit in cs.minted and unit not in added:
                added.add(unit)
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, ward or first["council"])
            contest_key = f"{election_key}:{slug(ward) if ward else 'council'}"
            contest = cs.key("gb.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=cs.key("gb.body", key),
                district_area_id=area,
                magnitude=places,
                round_number=1,
                electoral_system_id=systems["fptp" if places == 1 else "block"],
                default_result_kind="tacit" if tacit else "counted",
            )
            label = f"by-election, {day.day} {day:%B %Y}" if by else str(day.year)
            name(contest, f"{bodies[key]}, {ward} ward, {label}" if ward else f"{bodies[key]}, {label}")
            cs.add("slug", path=f"{election_path}/{slug(ward) if ward else 'council'}", entity_id=contest)
            source = sources[first["url"]]
            cs.add("citation", entity_id=contest, source_id=source)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="tacit" if tacit else "counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            publisher = COUNCILS[first["lad"]][0]
            if tacit and places is None:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNSTATED.format(publisher=publisher))
            elif tacit and lines.height < places:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=SHORT.format(
                        publisher=publisher, elected=lines.height, places=places, left=places - lines.height
                    ),
                )
            if first["caveat"]:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=first["caveat"])
            if first["context"]:
                cs.add("note", entity_id=result_set, kind="context", body_md=first["context"])
            elif first["counted"] is not None:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{publisher} prints {first['counted']:,} ballot papers included in the count and a "
                    f"turnout of {first['turnout']}%.",
                )
            elif first["issued"] is not None:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{publisher} prints {first['issued']:,} ballot papers issued.",
                )
            elif first["turnout"] is not None:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{publisher} gives turnout as {first['turnout']}% and prints no ballot or electorate counts.",
                )
            if not tacit:
                for measure, field in ((REGISTERED, "electorate"), (INVALID, "rejected")):
                    if first[field] is not None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=None,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=DIGITIZED if field == "rejected" and first["rejected_box"] is not None else 0,
                            value=first[field],
                        )
                if first["rejected_box"] is not None:
                    locate(
                        result_set, unit, None, INVALID, first["page"], first["rejected_page"], first["rejected_box"]
                    )
            for line in lines.iter_rows(named=True):
                option_key = f"{contest_key}:{line['number']}"
                person_key = f"{key}:{slug(line['name'])}"
                if person_key not in people:
                    people[person_key] = cs.key(f"{SOURCE}.person", person_key)
                    cs.add("person", id=people[person_key])
                    name(people[person_key], line["name"])
                party = party_of(line["description"])
                option = cs.key(f"{SOURCE}.option", option_key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_label=line["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", option_key),
                    person_id=people[person_key],
                    option_id=option,
                    role="head",
                    ballot_name=line["printed"],
                    member_of_party_id=party,
                    is_independent=PARTIES[line["description"]] == "independent",
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
                        flags=DIGITIZED if line["box"] is not None else 0,
                        value=line["votes"],
                    )
                    if line["box"] is not None:
                        locate(result_set, unit, option, VOTES, line["page"], line["box_page"], line["box"])
                if line["elected"]:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
