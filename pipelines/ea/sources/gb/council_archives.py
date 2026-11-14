import base64
import csv
import html
import io
import json
import re
import subprocess
import tempfile
from collections import Counter
from datetime import date
from difflib import SequenceMatcher, get_close_matches
from itertools import pairwise
from pathlib import Path
from uuid import UUID

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.names import display, foreign_preferred
from ea.store import DATA, connect

SOURCE = "gb.council_archives"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5
OGL = "https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/"
PAGES = DATA / "pages" / SOURCE
DIGITIZED = 8
DOVER, KENT, EPSOM, SURREY, BEDFORD, CALDERDALE, SHEPWAY = (
    "E07000108",
    "E10000016",
    "E07000208",
    "E10000030",
    "E06000055",
    "E08000033",
    "E07000112",
)
DOVER_SITE = "https://www.dover.gov.uk/asset-library"
EPSOM_SITE = "https://www.epsom-ewell.gov.uk/sites/default/files/documents/council/elections-and-voting"
CALDERDALE_SITE = "https://www.calderdale.gov.uk/council/democracy/electionresults/results.jsp"
CALDERDALE_WARDS = 18
LEEDS = "E08000035"
LEEDS_SITE = "https://datamillnorth.org/download/20jwj"
LEEDS_FILES = {
    1973: ("m55/1973%20-%20LCC%20Elections%2010%20May%201973.pdf", date(1973, 5, 10)),
    1975: ("615/1975%20-%20LCC%20Elections%201%20May%201975.pdf", date(1975, 5, 1)),
    1976: ("ntw/1976%20-%20LCC%20Elections%206%20May%201976.pdf", date(1976, 5, 6)),
    1978: ("ydt/1978%20-%20LCC%20Elections%204%20May%201978.pdf", date(1978, 5, 4)),
    1979: ("tep/1979%20-%20LCC%20Elections%203%20May%201979.pdf", date(1979, 5, 3)),
    1980: ("0zn/1980%20-%20LCC%20Elections%201%20May%201980.pdf", date(1980, 5, 1)),
    1982: ("yl0/1982%20-%20LCC%20Elections%206%20May%201982.pdf", date(1982, 5, 6)),
    1983: ("gpd/1983%20-%20LCC%20Elections%205%20May%201983.pdf", date(1983, 5, 5)),
    1984: ("6x8/1984%20-%20LCC%20Elections%203%20May%201984.pdf", date(1984, 5, 3)),
    1986: ("ems/1986%20-%20LCC%20Elections%208%20May%201986.pdf", date(1986, 5, 8)),
    1987: ("1sh/1987%20-%20LCC%20Elections%207%20May%201987.pdf", date(1987, 5, 7)),
    1988: ("3xt/1988%20-%20LCC%20Elections%205%20May%201988.pdf", date(1988, 5, 5)),
    1990: ("wxt/1990%20-%20LCC%20Elections%203%20May%201990.pdf", date(1990, 5, 3)),
    1991: ("szj/1991%20-%20LCC%20Elections%202%20May%201991.pdf", date(1991, 5, 2)),
    1992: ("wd7/1992%20-%20LCC%20Elections%207%20May%201992.pdf", date(1992, 5, 7)),
    1994: ("qvl/1994%20-%20LCC%20Elections%205%20May%201994.pdf", date(1994, 5, 5)),
    1995: ("wme/1985%20-%20LCC%20Elections%204%20May%201985.pdf", date(1995, 5, 4)),
    1996: ("l95/1996%20-%20LCC%20Elections%202%20May%201996.pdf", date(1996, 5, 2)),
    1998: ("1xp/1998%20-%20LCC%20Elections%207%20May%201998.pdf", date(1998, 5, 7)),
    1999: ("gj8/1999%20-%20LCC%20Elections%206%20May%201999.pdf", date(1999, 5, 6)),
    2000: ("gb1/2000%20-%20LCC%20Elections%204%20May%202000.pdf", date(2000, 5, 4)),
}
LEEDS_MODEL = "claude-haiku-5-5"
LEEDS_LONGEST = 1568
LEEDS_SPECKS = 40
LEEDS_COLUMNS = {1988: 1, 1990: 4, 1999: 5}
LEEDS_PAGE_COLUMNS = {(1995, 1): 4, (1996, 1): 3}
LEEDS_UPSIDE = {(1995, 1)}
LEEDS_ALL_OUT = {1973, 1980}
LEEDS_MARKED = {1973, 1980, 1982, 1983, 1984, 2000}
LEEDS_UNSURE = "?"
LEEDS_WARDS = (
    "AIREBOROUGH",
    "ARMLEY",
    "BARWICK & KIPPAX",
    "BEESTON",
    "BRAMLEY",
    "BURMANTOFTS",
    "CHAPEL ALLERTON",
    "CITY & HOLBECK",
    "COOKRIDGE",
    "GARFORTH & SWILLINGTON",
    "HALTON",
    "HAREHILLS",
    "HEADINGLEY",
    "HORSFORTH",
    "HUNSLET",
    "KIRKSTALL",
    "MIDDLETON",
    "MOORTOWN",
    "MORLEY NORTH",
    "MORLEY SOUTH",
    "NORTH",
    "OTLEY & WHARFEDALE",
    "PUDSEY NORTH",
    "PUDSEY SOUTH",
    "RICHMOND HILL",
    "ROTHWELL",
    "ROUNDHAY",
    "SEACROFT",
    "UNIVERSITY",
    "WEETWOOD",
    "WETHERBY",
    "WHINMOOR",
    "WORTLEY",
)
LEEDS_NUMBERED = (
    "Armley and Castleton",
    "Beeston and Holbeck",
    "Burmantofts and Richmond Hill",
    "Chapel Allerton and Scott Hall",
    "City and Woodhouse",
    "Cookridge and Weetwood",
    "East and West Hunslet",
    "Gipton and Whinmoor",
    "Harehills and Roundhay",
    "Bramley",
    "Halton",
    "Headingley",
    "Kirkstall",
    "Burley",
    "Middleton",
    "Moortown",
    "Osmondthorpe",
    "Seacroft",
    "Stanningley",
    "Talbot",
    "Wortley",
    "Morley Part 1",
    "Morley Part 2",
    "Pudsey Part 1",
    "Pudsey Part 2",
    "Aireborough",
    "Garforth Part 1",
    "Garforth Part 2",
    "Horsforth",
    "Otley",
    "Rothwell",
    "Wetherby",
)
LEEDS_TAIL = r"\s*\(.*$|\s+[A-Z]+/[A-Z/]+\)?$|\s+(LAB|CON|LIB|LIB DEM|COMM?|NF|ECOL|SDP|GRN|IND)\.?$"
LEEDS_LABELS = {
    **dict.fromkeys(("LAB", "LABOUR", "LAB/CO OP", "LAB/ CO OP"), "Labour Party"),
    **dict.fromkeys(("CON", "CONSERVATIVE", "OFF CON", "OFFICIAL CONSERVATIVE"), "Conservative Party"),
    **dict.fromkeys(
        (
            "LIB DEM",
            "L/DEM",
            "LIB/DEM",
            "LIBERAL DEMOCRAT",
            "YOUR LIBERAL DEMOCRAT TEAM",
            "SOCIAL AND LIBERAL DEMOCRAT",
            "SOCIAL AND LIBERAL DEMOCRATS",
            "SOCIAL & LIBERAL DEMOCRAT",
            "SOCIAL & LIBERAL DEMOCRATS",
            "SOCIAL AND LIBERAL DEMOCRATIC",
            "SOCIAL LIBERAL DEMOCRAT",
            "SLD",
            "LIBERAL SLD",
        ),
        "Liberal Democrats",
    ),
    "LIB": "Liberal",
    **dict.fromkeys(("SDP/LIB", "LIB/SDP", "SDP/ALNCE", "LIB/ALNCE", "SDP/LIB/ALNCE", "LIB/SDP/ALNCE"), "SDP/Lib All"),
    **dict.fromkeys(("SDP", "S D P", "SOCIAL DEMOCRATIC"), "Social Democratic Party"),
    **dict.fromkeys(("GRN", "GREEN", "GRN IST", "GRN ND", "GRN ST"), "Green Party"),
    **dict.fromkeys(("ECOL", "ECOLOGY"), "Ecology Party"),
    **dict.fromkeys(("COM", "COMM", "COMMUNIST"), "Communist"),
    **dict.fromkeys(("RV COMM", "RV  COMM"), "Revolutionary Communist"),
    **dict.fromkeys(("NAT FRONT", "NATFRONT", "NF"), "National Front"),
    "BNP": "BNP",
    **dict.fromkeys(("IND", "INDEP", "INDEPENDENT"), "Independent"),
    **dict.fromkeys(("IND LAB", "IND/LAB"), "Independent Labour"),
    "UK IND": "UK Independence Party",
    "LEFT AL": "Left Alliance",
    "MIL LAB": "Militant Labour",
}
LEEDS_HAND = Path(__file__).with_name("council_archives_leeds.tsv")
LEEDS_READS = {
    "a": "",
    "b": " Read each handwritten vote figure digit by digit before you write it, and count its digits; where a figure "
    "was altered, give the one that stands.",
    "c": " Some figures are smudged or written over the dotted leader: look at each one closely before you write it, "
    "and never carry a figure over from another candidate.",
}
LEEDS_SYSTEM = (
    "You read part of a scanned sheet from Leeds City Council giving the result of the poll at a city council "
    "election, ward by ward. Names are typed; parties and votes are often written by hand. Some years print a table "
    "with the columns Ward, Name of candidate, Home address, and Description, with the votes written beside each "
    "name: there, take the ward from the first column and the description as the party. Return every ward block in "
    "reading order, each column from top to bottom and the columns from left to right. For each ward give the "
    "heading exactly as printed (such as WARD NO. 3 (Burmantofts & Richmond Hill) or ARMLEY WARD), whether it is "
    "marked as continued, any printed note on the number of seats (such as TWO VACANCIES), and its box as "
    "[left, top, right, bottom] in thousandths of the image width and height. For each candidate give the name "
    "exactly as typed, the party exactly as written (such as LAB, CON, LIB, COMM, NF, ECOL, SDP, LIB DEM, or GRN), "
    "the votes as the digits written (null if no figure), any mark written beside the name or figure that shows the "
    "candidate was elected (such as a year, an R, a tick, a ring, or a box) copied as text or null, and any figure "
    "struck through. Skip the title, seat counts by party, turnout percentages, and other notes."
)
LEEDS_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "wards": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "continued": {"type": "boolean"},
                    "seats_note": {"type": ["string", "null"]},
                    "box": {"type": "array", "items": {"type": "integer"}},
                    "candidates": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "name": {"type": "string"},
                                "party": {"type": ["string", "null"]},
                                "votes": {"type": ["string", "null"]},
                                "mark": {"type": ["string", "null"]},
                                "struck": {"type": ["string", "null"]},
                            },
                            "required": ["name", "party", "votes", "mark", "struck"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["heading", "continued", "seats_note", "box", "candidates"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["wards"],
    "additionalProperties": False,
}
DOVER_NOTES = (
    "Dover District Council's typed summary of the district council election, ward by ward, with each candidate's "
    "name, description, and votes, the elected candidates, the seats, and the turnout as a percentage. It prints no "
    "electorate or ballot count, so turnout is not stored."
)
COLUMNS = {
    "dover-old": (135, 298, 470, 505),
    "dover-1999": (160, 325, 462, 505),
    "kent-1997": (155, 298, 466, 502),
    "epsom": (136, 280, 425, 500),
    "bedford": (158, 302, 470, 518),
}
FILES: list[dict] = [
    {
        "file": f"{DOVER_SITE}/1987-district-council-election-results.pdf",
        "parser": "dover-old",
        "code": DOVER,
        "day": date(1987, 5, 7),
        "publisher": "Dover District Council",
        "title": "District Council Election, 7 May 1987: results",
        "notes": DOVER_NOTES,
    },
    {
        "file": f"{DOVER_SITE}/1991-district-council-election-results.pdf",
        "parser": "dover-old",
        "code": DOVER,
        "day": date(1991, 5, 2),
        "publisher": "Dover District Council",
        "title": "District Council Election, 2 May 1991: results",
        "notes": DOVER_NOTES,
    },
    {
        "file": f"{DOVER_SITE}/1995-district-council-election-results.pdf",
        "parser": "dover-old",
        "code": DOVER,
        "day": date(1995, 5, 4),
        "publisher": "Dover District Council",
        "title": "District Council Election, 4 May 1995: results",
        "notes": DOVER_NOTES,
    },
    {
        "file": f"{DOVER_SITE}/1999-district-council-election-results.pdf",
        "parser": "dover-1999",
        "code": DOVER,
        "day": date(1999, 5, 6),
        "publisher": "Dover District Council",
        "title": "District Council Election, 6 May 1999: results",
        "notes": DOVER_NOTES + " A table of the new council's seats by party closes the file.",
    },
    {
        "file": f"{DOVER_SITE}/1997-kent-county-council-elections-all-dover-district-areas.pdf",
        "parser": "kent-1997",
        "code": KENT,
        "day": date(1997, 5, 1),
        "publisher": "Dover District Council",
        "title": "County Council Election, 1 May 1997: results in Dover district",
        "notes": "Dover District Council's typed summary of the Kent County Council election in the district's seven "
        "divisions, with each candidate's name, description, and votes, the elected candidate, the electorate, "
        "the ballot papers, and the percentage poll.",
    },
    {
        "file": f"{DOVER_SITE}/2001-kent-county-council-elections-all-dover-district-areas.pdf",
        "parser": "kent-2001",
        "code": KENT,
        "day": date(2001, 6, 7),
        "publisher": "Dover District Council",
        "title": "Kent County Council Election, 7 June 2001: declarations of result of poll in Dover district",
        "notes": "The deputy returning officer's declaration of result for each of the seven Kent County Council "
        "divisions in Dover district, one page each, with candidates, descriptions, votes, the elected candidate, "
        "rejected ballots, electorate, and ballot papers issued.",
    },
    {
        "file": "https://www.electionscentre.co.uk/wp-content/uploads/2015/06/Kent-County.pdf",
        "parser": "kent-handbook",
        "code": KENT,
        "day": None,
        "publisher": "The Elections Centre, University of Plymouth",
        "title": "Kent County Council Election Results 1973-2009",
        "notes": "Colin Rallings and Michael Thrasher's results book for the county council's elections, from the Local "
        "Elections Handbook, with each division's electorate, each candidate's name, party, and votes, and the turnout "
        "as a percentage. It does not mark the elected candidate; in a single-member division that is the candidate "
        "with the most votes, listed first. The council's 1997 and 2001 divisions outside Dover district come from it.",
    },
    {
        "file": f"{EPSOM_SITE}/BoroughCouncilElectionResults6May1999.pdf",
        "parser": "epsom",
        "code": EPSOM,
        "day": date(1999, 5, 6),
        "publisher": "Epsom and Ewell Borough Council",
        "title": "Borough Council Election Results, 6 May 1999",
        "notes": "The borough council's summary, ward by ward, with each candidate's name, description, and votes, the "
        "elected candidates, the electorate, the seats, the ballot papers, and the percentage poll.",
    },
    {
        "file": f"{EPSOM_SITE}/SCCElectionResults1965to2005.pdf",
        "parser": "surrey",
        "code": SURREY,
        "day": None,
        "publisher": "Epsom and Ewell Borough Council",
        "title": "Surrey County Council Election Results, 1965 to 2005",
        "notes": "The borough council's history of the Surrey County Council elections in its five county divisions, "
        "one page each, from 1965 to 2005, with each candidate's name, party from 1981 on, and votes, the elected "
        "candidate, and the turnout as a percentage. It gives the month and year of each poll, not the day; the "
        "county elections from 1973 on are dated to the day England's county councils polled that month. Seats won "
        "without a poll print Uncontested.",
    },
    {
        "file": "https://www.bedford.gov.uk/files/boroughelection7may1998.pdf/download",
        "parser": "bedford",
        "code": BEDFORD,
        "day": date(1998, 5, 7),
        "publisher": "Bedford Borough Council",
        "title": "Borough Council Election, 7 May 1998: results",
        "notes": "The borough council's summary, ward by ward, with each candidate's name, description, and votes, the "
        "elected candidate, the electorate, the seats, the ballot papers, and the percentage poll.",
    },
    {
        "file": "https://www.folkestone-hythe.gov.uk/downloads/file/1229/shepway-district-council-results-1973-to-2011",
        "parser": "shepway",
        "code": SHEPWAY,
        "day": None,
        "kind": "academic",
        "publisher": "The Elections Centre, Plymouth University",
        "title": "Shepway District Council Election Results 1973-2011",
        "license": None,
        "notes": "Colin Rallings and Michael Thrasher's ward-by-ward results of every Shepway District Council "
        "election from 1973 to 2011, a scan with no text layer, published on Folkestone and Hythe District Council's "
        "website. It is not an official canvass: the Elections Centre says the results of the years after the 1973 "
        "reorganisation were collected by Alan Willis largely from local newspaper reports, and those from the "
        "mid-1980s on from the council. It prints each ward's electorate and seats, each candidate's name, party, "
        "and votes, the elected candidates in bold, and the turnout as a percentage, but no ballot count, so turnout "
        "is not stored. It gives no polling days; each election is dated to the day England's district councils "
        "polled that year. Figures were read from the scan by OCR and checked against the printed vote shares.",
    },
]
CALDERDALE_ELECTIONS = {
    2: (date(1999, 5, 6), "Election of Local Councillors 1999"),
    4: (date(1999, 10, 29), "Elland by-election, 29 October 1999"),
    5: (date(2000, 5, 4), "Election of Local Councillors 2000"),
}
LEEDS_NATIONAL = {date(1973, 5, 10): "1973 metropolitan district elections in England"}
LEEDS_NOTES = (
    "Scanned typed and handwritten result sheets for each Leeds City Council election, published by the council as "
    "open data, with each ward's candidates, parties, and votes; some years mark the elected candidates. Figures "
    "were read three times from the page images and disagreements settled by eye against the scan."
)
CALDERDALE_NOTES = (
    "Calderdale Council's election results pages, one per ward, with each candidate's surname and forenames, party, "
    "votes, and the elected candidate, and the ward's eligible voters, total turnout, and rejected papers."
)
SURREY_DAYS = {
    1973: date(1973, 4, 12),
    1977: date(1977, 5, 5),
    1981: date(1981, 5, 7),
    1985: date(1985, 5, 2),
    1989: date(1989, 5, 4),
    1993: date(1993, 5, 6),
    1997: date(1997, 5, 1),
    2001: date(2001, 6, 7),
}
HELD = {(SURREY, 2005)}
KENT_DAYS = {1997: date(1997, 5, 1), 2001: date(2001, 6, 7)}
DOVER_DIVISIONS = {"Deal East", "Deal West", "Dover Central", "Dover Rural", "Dover South", "Dover West", "Sandwich"}
SHEPWAY_DAYS = {
    1973: date(1973, 6, 7),
    1976: date(1976, 5, 6),
    1979: date(1979, 5, 3),
    1983: date(1983, 5, 5),
    1987: date(1987, 5, 7),
    1991: date(1991, 5, 2),
    1995: date(1995, 5, 4),
    1999: date(1999, 5, 6),
}
SCAN_DPI = 300
SCAN_COLUMNS = ((180, 870), (880, 1570), (1580, 2270))
SCAN_ROWS = (150, 3250)
SCAN_PARTIES = {
    "Con": "Conservative",
    "Lab": "Labour",
    "Lib": "Liberal",
    "LD": "Liberal Democrat",
    "Lib/SDP": "SDP/Lib All",
    "Ind": "Independent",
}
JUNK = re.compile(r"^[^A-Za-z]*(?:[|_=\"'.:,;]\s*)+")
MONTHS = {
    month: index
    for index, month in enumerate(
        ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October"]
        + ["November", "December"],
        1,
    )
}
FOOTER = re.compile(r"Page \d+ of|\.doc|ELECT\\|Total % Turnout|Composition of New Council")
LABEL = re.compile(
    r"(Electorate|Seats|Ballot Papers|% Poll|% Turnout|Uncontested)\s*([\d.]*)|([\d.]+)%\s*Poll", re.IGNORECASE
)
FIXES = {"Marita Helga lsabella Gerda Gerrard": "Marita Helga Isabella Gerda Gerrard"}
LABOUR = ("wikidata", "Q9630")
CONSERVATIVE = ("wikidata", "Q9626")
LIBERAL_DEMOCRATS = ("wikidata", "Q9624")
ALLIANCE = ("wikidata", "Q7389478")
PARTIES = {
    **dict.fromkeys(
        (
            "Labour Party",
            "Labour",
            "Lab",
            "The Labour Party Candidate",
            "Labour Party Candidate",
            "Labour and Co-Operative Party",
            "The Labour and Co-Operative Party Candidate",
            "Labour & Co-operative Party",
            "The Labour & Co-Operative Party Candidate",
            "Labour Co-Op Candidate",
            "Labour-Co-Op",
            "Labour/Co-Op",
            "Labour Party Candidate",
        ),
        LABOUR,
    ),
    **dict.fromkeys(
        (
            "Conservative Party",
            "Conservative",
            "Con",
            "The Conservative Party Candidate",
            "Conservative Party Candidate",
            "Official Conservative Party",
            "Official Conservative Candidate",
        ),
        CONSERVATIVE,
    ),
    **dict.fromkeys(
        (
            "Liberal Democrat",
            "Liberal Democrats",
            "Lib Dem",
            "LibDem",
            "Lib.Dem",
            "LD",
            "Liberal Democrat Focus",
            "Liberal Democrat Focus Team",
            "Liberal Democrat and Focus Team",
            "Liberal Democrat & Focus Team",
            "SLD",
        ),
        LIBERAL_DEMOCRATS,
    ),
    "SDP/Lib All": ALLIANCE,
    "Lib/SDP": ALLIANCE,
    "Green Party": ("gb.party", "green-party"),
    "Green": ("gb.party", "green-party"),
    "UK Independence Party": ("wikidata", "Q10647"),
    "UK Ind Party": ("wikidata", "Q10647"),
    "British National Party": ("gb.party", "british-national-party"),
    "BNP": ("gb.party", "british-national-party"),
    "Communist": ("wikidata", "Q592068"),
    "Liberal": ("wikidata", "Q622441"),
    "Liberal Party": ("gb.party", "liberal-party"),
    "Liberal Party (1989)": ("gb.party", "liberal-party-1989"),
    "SDP": ("gb.party", "social-democratic-party-1981"),
    "SDP (1990)": ("gb.party", "social-democratic-party-1990"),
    "Ecology Party": ("gb.party", "ecology-party"),
    "National Front": ("gb.party", "national-front"),
    "Independent Labour": ("gb.party", "independent-labour"),
    "Militant Labour": ("gb.party", "militant-labour"),
}
INDEPENDENT = {"Independent", "Ind"}


def fetch() -> None:
    for entry in FILES:
        raw.fetch(SOURCE, entry["file"], suffix=".pdf")
    for election in CALDERDALE_ELECTIONS:
        for area in range(1, CALDERDALE_WARDS + 1):
            raw.fetch(SOURCE, f"{CALDERDALE_SITE}?election={election}&area={area}", suffix=".html")
    price_in, price_out = batch.PRICES[LEEDS_MODEL]
    wanted: dict[str, batch.Want] = {}
    for year in LEEDS_FILES:
        path = str(raw.fetch(SOURCE, leeds_url(year), suffix=".pdf").path)
        for page in range(1, pages(path) + 1):
            for index, (_, piece) in enumerate(leeds_pieces(leeds_page(path, year, page), leeds_columns(year, page))):
                buffer = io.BytesIO()
                piece.save(buffer, "PNG")
                for read, extra in LEEDS_READS.items():
                    params = MessageCreateParamsNonStreaming(
                        model=LEEDS_MODEL,
                        max_tokens=16000,
                        system=LEEDS_SYSTEM + extra,
                        messages=[
                            {
                                "role": "user",
                                "content": [
                                    ImageBlockParam(
                                        type="image",
                                        source={
                                            "type": "base64",
                                            "media_type": "image/png",
                                            "data": base64.b64encode(buffer.getvalue()).decode(),
                                        },
                                    ),
                                    TextBlockParam(
                                        type="text",
                                        text=f"Read every ward on this part of the {LEEDS_FILES[year][1]:%-d %B %Y} sheet.",
                                    ),
                                ],
                            }
                        ],
                        output_config={"effort": "low", "format": {"type": "json_schema", "schema": LEEDS_SCHEMA}},
                    )
                    estimate = (piece.width * piece.height / 750 * price_in + 4000 * price_out) / 1e6
                    wanted[leeds_key(read, year, page, index)] = (params, estimate)
    print(batch.ensure(SOURCE, "leeds", wanted)[1])


def leeds_url(year: int) -> str:
    return f"{LEEDS_SITE}/{LEEDS_FILES[year][0]}"


def leeds_columns(year: int, page: int) -> int:
    return LEEDS_PAGE_COLUMNS.get((year, page), LEEDS_COLUMNS.get(year, 2))


def leeds_piece(year: int, page: int, index: int | str) -> str:
    columns = leeds_columns(year, page)
    return f"{index}{f'of{columns}' if columns > 2 else ''}{'u' if (year, page) in LEEDS_UPSIDE else ''}"


def leeds_key(read: str, year: int, page: int, index: int) -> str:
    return f"anthropic-batch:{LEEDS_MODEL}/leeds-{read}/{year}/{page}/{leeds_piece(year, page, index)}"


def leeds_page(path: str, year: int, page: int) -> Image.Image:
    with tempfile.TemporaryDirectory() as folder:
        stem = f"{folder}/page"
        subprocess.run(
            ["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile", path, stem],
            check=True,
        )
        return Image.open(f"{stem}.png").convert("L").rotate(270 if (year, page) in LEEDS_UPSIDE else 90, expand=True)


def leeds_box(year: int, page: int, box: list[int] | None) -> list[int] | None:
    if box is None or len(box) != 4:
        return None
    left, top, right, bottom = (min(max(value, 0), 1000) for value in box)
    if (year, page) in LEEDS_UPSIDE:
        return [min(top, bottom), 1000 - max(left, right), max(top, bottom), 1000 - min(left, right)]
    return [1000 - max(top, bottom), min(left, right), 1000 - min(top, bottom), max(left, right)]


def leeds_pieces(image: Image.Image, columns: int) -> list[tuple[tuple[int, int, int, int], Image.Image]]:
    width, height = image.size
    ink = image.crop((0, int(height * 0.08), width, int(height * 0.97))).point(lambda v: 1 if v < 128 else 0)
    data = ink.tobytes()
    sums = [sum(data[x : len(data) : width * 2]) for x in range(width)]
    span = 12
    edges = [0]
    for part in range(1, columns):
        middle = width * part // columns
        window = range(middle - int(width * 0.35 / columns), middle + int(width * 0.35 / columns) - span)
        cut = min(window, key=lambda x: (sum(sums[x : x + span]), abs(x - middle)))
        if columns == 2 and sum(sums[cut : cut + span]) > LEEDS_SPECKS:
            break
        edges.append(cut + span // 2)
    edges.append(width)
    pieces = []
    for left, right in pairwise(edges):
        box = (left, 0, right, height)
        piece = image.crop(box)
        piece.thumbnail((LEEDS_LONGEST, LEEDS_LONGEST))
        pieces.append((box, piece))
    return pieces


def leeds_reads(year: int) -> dict[str, list[dict]]:
    path = str(raw.cached(SOURCE, leeds_url(year)).path)
    keys = raw.keys(SOURCE)
    reads: dict[str, list[dict]] = {}
    for read in LEEDS_READS:
        found_lines = reads.setdefault(read, [])
        for page in range(1, pages(path) + 1):
            prefix = f"anthropic-batch:{LEEDS_MODEL}/leeds-{read}/{year}/{page}/"
            pieces = sorted(
                (int(key.removeprefix(prefix).split("of")[0].rstrip("u")), key)
                for key in keys
                if key.startswith(prefix) and re.fullmatch(leeds_piece(year, page, r"\d+"), key.removeprefix(prefix))
            )
            if not pieces:
                continue
            image = leeds_page(path, year, page)
            boxes = [box for box, _ in leeds_pieces(image, leeds_columns(year, page))]
            for index, key in pieces:
                left, _, right, _ = boxes[index]
                message = json.loads(raw.cached(SOURCE, key).path.read_bytes())
                if message.get("stop_reason") != "end_turn":
                    continue
                text = next(block["text"] for block in message["content"] if block["type"] == "text")
                for ward in json.loads(text)["wards"]:
                    if not leeds_ward(ward["heading"]):
                        continue
                    for entry in ward["candidates"]:
                        found_lines.append(
                            {
                                **entry,
                                "heading": leeds_ward(ward["heading"]),
                                "seats_note": ward["seats_note"],
                                "page": page,
                                "piece": index,
                                "box": [
                                    round((left + ward["box"][0] * (right - left) / 1000) * 1000 / image.width),
                                    ward["box"][1],
                                    round((left + ward["box"][2] * (right - left) / 1000) * 1000 / image.width),
                                    ward["box"][3],
                                ]
                                if len(ward["box"]) == 4
                                else None,
                                "key": re.sub(r"[^A-Z]", "", re.sub(LEEDS_TAIL, "", entry["name"].upper().strip())),
                            }
                        )
    return reads


def leeds_ward(heading: str) -> str:
    text = heading.upper()
    if "ELECTORAL DIVISION" in text or "W.Y.C.C" in text:
        return ""
    number = re.match(r"WARD NO\.? ?(\d+)", text)
    if number:
        return LEEDS_NUMBERED[int(number[1]) - 1]
    inner = re.search(r"\(([^)]*)\)?", text)
    if "WARD NO" in text and inner:
        text = inner[1]
    text = re.sub(r"\bWARD\b|\(.*|NO\.? ?\d+|CONT.*|[^A-Z& ]", " ", text)
    text = " ".join(text.replace(" AND ", " & ").replace("&", " & ").split())
    return next(iter(get_close_matches(text, LEEDS_WARDS, 1, 0.6)), text)


def leeds_votes(value: str | None) -> int | None:
    digits = re.sub(r"[,.\s]", "", value or "")
    return int(digits) if digits.isdigit() else None


def majority(values: list) -> object:
    value, count = Counter(values).most_common(1)[0]
    return value if count >= 2 or len(values) == 1 else LEEDS_UNSURE


def leeds_party(year: int, party: str) -> str:
    text = re.sub(r"[^A-Z/& ]", "", party.upper().replace("-", " ")).strip()
    text = re.sub(r"^THE | CANDIDATE$| PARTY$| PARTY CANDIDATE$", "", text).strip()
    label = LEEDS_LABELS.get(" ".join(text.split()), party)
    if label == "Liberal":
        return "Liberal Party" if year < 1989 else "Liberal Party (1989)"
    if label == "Social Democratic Party":
        return "SDP" if year <= 1990 else "SDP (1990)"
    return label


def leeds() -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    wards: list[dict] = []
    hand = {
        (int(line["year"]), line["candidate"]): int(line["votes"]) if line["votes"] else None
        for line in csv.DictReader(LEEDS_HAND.open(), delimiter="\t")
    }
    for year, (_, day) in LEEDS_FILES.items():
        reads = leeds_reads(year)
        clusters: list[dict[str, dict]] = []
        for read, entries in reads.items():
            taken: set[int] = set()
            for entry in entries:
                scores = [
                    (
                        SequenceMatcher(None, entry["key"], first["key"]).ratio()
                        + 0.1 * (entry["heading"] == first["heading"]),
                        index,
                    )
                    for index, cluster in enumerate(clusters)
                    if read not in cluster and index not in taken
                    for first in [next(iter(cluster.values()))]
                ]
                score, index = max(scores, default=(0.0, -1))
                if score >= 0.85:
                    clusters[index][read] = entry
                    taken.add(index)
                else:
                    clusters.append({read: entry})
                    taken.add(len(clusters) - 1)
        entry = {"code": LEEDS, "file": leeds_url(year), "day": day}
        by_ward: dict[str, list[dict]] = {}
        unsure = 0
        for cluster in clusters:
            seen = list(cluster.values())
            name = majority([line["key"] for line in seen])
            if name == LEEDS_UNSURE:
                name = max(seen, key=lambda line: len(line["key"]))["key"]
            pick = {
                "ward": majority([line["heading"] for line in seen]),
                "votes": hand.get((year, str(name)), majority([leeds_votes(line["votes"]) for line in seen])),
                "party": majority([line["party"] for line in seen]),
                "mark": majority([bool(line["mark"]) for line in seen]) is True,
                "hand": (year, str(name)) in hand,
            }
            label = next(line["name"] for line in seen if line["key"] == name)
            if len(seen) < 2 and len(reads) > 1:
                continue
            if LEEDS_UNSURE in pick.values():
                unsure += 1
                print(
                    f"leeds {year}: unsure {[(read, line['heading'], line['name'], line['party'], line['votes'], line['mark'], line['page'], line['piece']) for read, line in cluster.items()]}"
                )
                continue
            by_ward.setdefault(str(pick["ward"]), []).append(
                {
                    **pick,
                    "name": label,
                    "page": seen[0]["page"],
                    "box": seen[0]["box"],
                    "seats": {line["seats_note"] for line in seen},
                }
            )
        if unsure:
            continue
        for ward_name, found_lines in by_ward.items():
            if any(line["votes"] is None for line in found_lines):
                print(f"leeds {year}: {ward_name} has a figure cut off the scan")
                continue
            notes = set().union(*(line["seats"] for line in found_lines)) - {None}
            seats = 3 if year in LEEDS_ALL_OUT else 2 if any("TWO" in str(note) for note in notes) else 1
            marked = [line for line in found_lines if line["mark"]]
            elected = year in LEEDS_MARKED and len(marked) == seats
            ward = {
                "name": ward_name
                if ward_name in LEEDS_NUMBERED
                else " ".join(word if word == "&" else word.capitalize() for word in ward_name.split()).replace(
                    "&", "and"
                ),
                "code": LEEDS,
                "day": day,
                "seats": seats,
                "box": leeds_box(year, found_lines[0]["page"], found_lines[0]["box"]),
            }
            wards.append(ward)
            for line in found_lines:
                printed = re.sub(LEEDS_TAIL, "", str(line["name"]).strip())
                parts = printed.replace(".", ". ").split()
                letters = ""
                while len(parts) > 1 and len(parts[-1].rstrip(".")) == 1:
                    letters = f"{parts.pop().rstrip('.')}. {letters}"
                surname = " ".join(parts)
                label = display(printed) if re.search(r"[a-z]{2}", printed) else f"{letters}{display(surname)}"
                rows.append(
                    candidate(
                        entry,
                        ward,
                        line["page"],
                        label,
                        leeds_party(year, str(line["party"] or "")),
                        int(str(line["votes"])),
                        elected and bool(line["mark"]),
                    )
                )
        print(f"leeds {year}: {len(by_ward)} wards")
    return rows, wards


def found(pattern: str, content: str, flags: int = 0) -> re.Match[str]:
    match = re.search(pattern, content, flags)
    if match is None:
        raise ValueError(f"no match for {pattern}")
    return match


def pages(path: str) -> int:
    info = subprocess.run(["pdfinfo", path], capture_output=True, text=True, check=True).stdout
    return int(found(r"Pages:\s+(\d+)", info)[1])


def words(path: str, page: int) -> list[tuple[float, float, str]]:
    out = subprocess.run(
        ["pdftotext", "-f", str(page), "-l", str(page), "-bbox", path, "-"], capture_output=True, text=True, check=True
    ).stdout
    return [
        (float(x), float(y), html.unescape(word))
        for x, y, word in re.findall(r'<word xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>([^<]*)</word>', out)
    ]


def lines(page_words: list[tuple[float, float, str]]) -> list[list[tuple[float, float, str]]]:
    grouped: list[list[tuple[float, float, str]]] = []
    for word in sorted(page_words, key=lambda w: (w[1], w[0])):
        if grouped and word[1] - grouped[-1][0][1] < 4:
            grouped[-1].append(word)
        else:
            grouped.append([word])
    for line in grouped:
        line.sort()
    return grouped


def join(line: list[tuple[float, float, str]], low: float, high: float) -> str:
    return " ".join(word for x, y, word in line if low <= x < high)


def candidate(entry: dict, ward: dict, page: int, name: str, description: str, votes: int | None, elected: bool):
    return {
        "code": entry["code"],
        "file": entry["file"],
        "at": ward,
        "page": page,
        "name": name,
        "description": description or None,
        "votes": votes,
        "elected": elected,
    }


def table(entry: dict) -> tuple[list[dict], list[dict]]:
    path = str(raw.cached(SOURCE, entry["file"]).path)
    name_x, description_x, votes_x, result_x = COLUMNS[entry["parser"]]
    wards: list[dict] = []
    rows: list[dict] = []
    for page in range(1, pages(path) + 1):
        grouped = lines(words(path, page))
        top = next(i for i, line in enumerate(grouped) if any(w[2] in ("Candidates", "Candidate") for w in line))
        last_ward = False
        pending = ""
        for line in grouped[top + 1 :]:
            text = " ".join(w[2] for w in line)
            if FOOTER.search(text):
                break
            left = join(line, 0, name_x)
            if left:
                labels = [m for m in LABEL.finditer(left)]
                if labels:
                    for label in labels:
                        key = (label[1] or "% Poll").lower()
                        value = label[2] if label[1] else label[3]
                        if key == "uncontested":
                            wards[-1]["tacit"] = True
                        elif key == "seats":
                            wards[-1]["seats"] = int(value)
                        elif key == "electorate":
                            wards[-1]["registered"] = int(value)
                        elif key == "ballot papers":
                            wards[-1]["ballots"] = int(value)
                        else:
                            wards[-1]["percent"] = float(value)
                    last_ward = False
                elif last_ward:
                    wards[-1]["name"] += f" {left}"
                else:
                    wards.append({"name": left, "code": entry["code"]})
                    last_ward = True
            name = join(line, name_x, description_x)
            description = join(line, description_x, votes_x)
            votes = join(line, votes_x, result_x).replace(",", "")
            result = join(line, result_x, 10_000)
            if not (name or description or votes or result):
                continue
            last_ward = False
            if not (name or votes or result) and description.endswith("-"):
                pending = description
                continue
            if votes.isdigit() or result == "Elected":
                if pending:
                    description = f"{pending}{description}" if description else pending
                    pending = ""
                rows.append(
                    candidate(entry, wards[-1], page, name, description, int(votes) if votes else None, bool(result))
                )
                if result not in ("", "Elected"):
                    raise ValueError(f"{entry['file']} page {page}: result {result}")
            elif votes:
                raise ValueError(f"{entry['file']} page {page}: votes {votes}")
            else:
                rows[-1]["name"] = f"{rows[-1]['name']} {name}".strip()
                previous = rows[-1]["description"] or ""
                glue = "" if previous.endswith("-") else " "
                rows[-1]["description"] = f"{previous}{glue}{description}".strip() or None
    for ward in wards:
        ward["name"] = ward["name"].replace("’", "'")
    return rows, wards


def kent_2001(entry: dict) -> tuple[list[dict], list[dict]]:
    path = str(raw.cached(SOURCE, entry["file"]).path)
    rows: list[dict] = []
    wards: list[dict] = []
    for page in range(1, pages(path) + 1):
        layout = subprocess.run(
            ["pdftotext", "-layout", "-f", str(page), "-l", str(page), path, "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        division = display(found(r"^\s*(.+?) ELECTORAL DIVISION", layout, re.MULTILINE)[1].strip())
        figures = found(r"Vacant Seats:\s*(\d+)\s+Electorate:\s*(\d+)\s+Ballot Papers Issued:\s*(\d+)", layout)
        rejected = re.search(r"TOTAL REJECTED\s+(\d+)", layout)
        ward = {
            "name": division,
            "code": entry["code"],
            "seats": int(figures[1]),
            "registered": int(figures[2]),
            "ballots": int(figures[3]),
            "rejected": int(rejected[1]) if rejected else None,
        }
        wards.append(ward)
        page_words = words(path, page)
        top = max(y for x, y, word in page_words if word == "votes") + 6
        bottom = min(y for x, y, word in page_words if word == "And" and y > top)
        for line in lines([w for w in page_words if top < w[1] < bottom]):
            surname = join(line, 0, 158)
            forenames = join(line, 158, 258)
            description = join(line, 258, 440)
            votes = join(line, 440, 478)
            result = join(line, 478, 10_000)
            if not (surname or votes or description) and forenames:
                rows[-1]["extra"] = f"{rows[-1].get('extra', '')} {forenames}".strip()
                continue
            if not votes.isdigit() or result not in ("", "ELECTED"):
                raise ValueError(f"Kent 2001 page {page}: line {line}")
            rows.append(candidate(entry, ward, page, forenames, description, int(votes), bool(result)))
            rows[-1]["surname"] = display(surname)
    for row in rows:
        extra = row.pop("extra", None)
        surname = row.pop("surname")
        if extra and extra.startswith("(commonly"):
            known = display(found(r"known as (\w+)\)", extra)[1])
            row["name"] = f"{row['name']} {surname} “{known}”"
        else:
            row["name"] = f"{row['name']} {extra} {surname}" if extra else f"{row['name']} {surname}"
    return rows, wards


def surrey(entry: dict) -> tuple[list[dict], list[dict]]:
    path = str(raw.cached(SOURCE, entry["file"]).path)
    rows: list[dict] = []
    wards: list[dict] = []
    for page in range(1, pages(path) + 1):
        grouped = lines(words(path, page))
        division = next(
            " ".join(w[2] for w in line) for line in grouped if any(w[2] == "Division" for w in line)
        ).removesuffix(" Division")
        division = division.replace("&", "and")
        header = next(line for line in grouped if any(w[2] == "Candidates" for w in line))
        at = {word: x - 4 for x, y, word in header}
        bounds = (at["Candidates"], at["Party"], at["Votes"], at["Result"], at["Turnout"])
        for line in grouped[grouped.index(header) + 1 :]:
            text = " ".join(w[2] for w in line)
            if FOOTER.search(text):
                break
            left = join(line, 0, bounds[0])
            when = re.fullmatch(r"(\w+) (\d{4})", left)
            if when:
                year = int(when[2])
                ward = {"name": division, "code": entry["code"], "year": year}
                if year in SURREY_DAYS:
                    ward["day"] = SURREY_DAYS[year]
                else:
                    ward["day"] = date(year, MONTHS[when[1]], 1)
                    ward["precision"] = "month"
                wards.append(ward)
            elif left == "(By election)":
                wards[-1]["by"] = True
                wards[-1]["precision"] = "month"
                wards[-1]["day"] = date(wards[-1]["year"], wards[-1]["day"].month, 1)
            elif left:
                raise ValueError(f"Surrey page {page}: {left}")
            name = join(line, bounds[0], bounds[1])
            party = join(line, bounds[1], bounds[2])
            votes = join(line, bounds[2], bounds[3]).replace(",", "")
            result = join(line, bounds[3], bounds[4])
            if not name:
                continue
            if votes not in ("Uncontested",) and not votes.isdigit():
                raise ValueError(f"Surrey page {page}: votes {votes}")
            if result not in ("", "Elected"):
                raise ValueError(f"Surrey page {page}: result {result}")
            ward = wards[-1]
            if votes == "Uncontested":
                ward["tacit"] = True
            rows.append(
                candidate(
                    entry,
                    ward,
                    page,
                    name,
                    "" if party == "-" else party,
                    None if votes == "Uncontested" else int(votes),
                    bool(result),
                )
            )
    for ward in wards:
        ward["seats"] = 1
    kept = [ward for ward in wards if (ward["code"], ward["day"].year) not in HELD]
    return [row for row in rows if any(row["at"] is ward for ward in kept)], kept


def kent_handbook(entry: dict) -> tuple[list[dict], list[dict]]:
    path = str(raw.cached(SOURCE, entry["file"]).path)
    rows: list[dict] = []
    wards: list[dict] = []
    year = None
    for page in range(1, pages(path) + 1):
        page_words = words(path, page)
        starts = sorted({round(x - 106.1) for x, y, word in page_words if word == "vote"})
        edges = [start - 4 for start in starts] + [10_000]
        for index in range(len(starts)):
            column = [w for w in page_words if edges[index] <= w[0] < edges[index + 1]]
            ward = None
            for line in lines(column):
                text = " ".join(w[2] for w in line)
                heading = re.fullmatch(r"Kent (\d{4})", text)
                division = re.fullmatch(r"(.+?) \((\d+)\)", text)
                if heading:
                    year = int(heading[1])
                elif division and year in KENT_DAYS:
                    name = division[1].replace("&", "and")
                    ward = (
                        None
                        if name in DOVER_DIVISIONS
                        else {
                            "name": name,
                            "code": entry["code"],
                            "day": KENT_DAYS[year],
                            "seats": 1,
                            "registered": int(division[2]),
                        }
                    )
                    if ward:
                        wards.append(ward)
                        first = True
                elif ward and not text.startswith(("vote", "Turnout")):
                    *label, party, votes, _ = [w[2] for w in line]
                    if not votes.replace(",", "").isdigit():
                        raise ValueError(f"Kent handbook page {page}: {text}")
                    label = " ".join(label).replace("*", "").replace(" Ms.", "")
                    rows.append(candidate(entry, ward, page, label, party, int(votes.replace(",", "")), first))
                    first = False
                elif ward and text.startswith("Turnout"):
                    ward["turnout"] = float(text.split()[1])
                    ward = None
    return rows, wards


def text(cell: str) -> str:
    return re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", cell))).strip()


def calderdale() -> tuple[list[dict], list[dict]]:
    rows: list[dict] = []
    wards: list[dict] = []
    for election, (day, title) in CALDERDALE_ELECTIONS.items():
        for area in [3] if election == 4 else range(1, CALDERDALE_WARDS + 1):
            url = f"{CALDERDALE_SITE}?election={election}&area={area}"
            content = Path(raw.cached(SOURCE, url).path).read_text(encoding="utf-8", errors="replace")
            match = re.search(r'summary="candidates name[^"]*">(.*?)</table>', content, re.DOTALL)
            if match is None:
                continue
            ward_name = found(r"\d{2}/\d{2}/\d{4} (.+?) Menu :", text(content))[1]
            figures = found(
                r"(?:eligible voters (\d+) )?Total turnout (\d+) (?:\([\d.]+%\) )?Papers rejected (\d+)",
                text(content),
            )
            ward = {
                "name": ward_name,
                "code": CALDERDALE,
                "day": day,
                "by": election == 4,
                "registered": int(figures[1]) if figures[1] else None,
                "ballots": int(figures[2]),
                "rejected": int(figures[3]),
                "url": url,
            }
            wards.append(ward)
            entry = {"code": CALDERDALE, "file": url, "day": day}
            for line in re.findall(r"<tr>(.*?)</tr>", match[1], re.DOTALL)[1:]:
                cells = re.findall(r"<td[^>]*>(.*?)</td>", line, re.DOTALL)
                surname = text(found(r"<strong>(.*?)</strong>", cells[0], re.DOTALL)[1])
                forenames = text(cells[0].split("<br />", 1)[1]) if "<br />" in cells[0] else ""
                rows.append(
                    candidate(
                        entry,
                        ward,
                        0,
                        f"{forenames} {surname}".strip(),
                        text(cells[1]),
                        int(text(cells[2])),
                        text(cells[3]) == "Elected",
                    )
                )
    return rows, wards


def ocr_lines(image: Image.Image, left: int, top: int) -> list[list[dict]]:
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        image.save(handle.name)
        out = subprocess.run(
            ["tesseract", handle.name, "-", "--psm", "6", "tsv"], capture_output=True, text=True, check=True
        ).stdout
    grouped: dict[tuple[str, str, str], list[dict]] = {}
    for row in csv.DictReader(io.StringIO(out), delimiter="\t", quoting=csv.QUOTE_NONE):
        if row["level"] == "5" and row["text"].strip():
            x, y, width, height = (int(row[key]) for key in ("left", "top", "width", "height"))
            word = {"text": row["text"], "box": (left + x, top + y, left + x + width, top + y + height)}
            grouped.setdefault((row["block_num"], row["par_num"], row["line_num"]), []).append(word)
    return list(grouped.values())


def ink(image: Image.Image, x: int, y: int) -> bool:
    value = image.getpixel((x, y))
    return isinstance(value, int | float) and value < 128


def stroke(image: Image.Image, words: list[dict]) -> float:
    runs: list[int] = []
    for word in words:
        x0, y0, x1, y1 = word["box"]
        crop = image.crop((x0, y0, x1, y1))
        for y in range(crop.height):
            run = 0
            for x in range(crop.width):
                if ink(crop, x, y):
                    run += 1
                elif run:
                    runs.append(run)
                    run = 0
            if run:
                runs.append(run)
    return sum(runs) / max(len(runs), 1)


def superscript(image: Image.Image, words: list[dict]) -> int:
    x0 = min(w["box"][0] for w in words)
    y0 = min(w["box"][1] for w in words)
    x1 = max(w["box"][2] for w in words)
    y1 = max(w["box"][3] for w in words)
    crop = image.crop((x0, y0, x1 + 40, y1))
    middle = crop.height // 2
    edge = max(
        (x for x in range(crop.width) if any(ink(crop, x, y) for y in range(middle, crop.height))),
        default=0,
    )
    tail = crop.crop((edge + 1, 0, crop.width, middle + 2))
    if not any(ink(tail, x, y) for x in range(tail.width) for y in range(tail.height)):
        return 1
    big = Image.new("L", (tail.width * 4 + 80, tail.height * 4 + 80), 255)
    big.paste(tail.resize((tail.width * 4, tail.height * 4)), (40, 40))
    with tempfile.NamedTemporaryFile(suffix=".png") as handle:
        big.save(handle.name)
        digit = subprocess.run(
            ["tesseract", handle.name, "-", "--psm", "10", "-c", "tessedit_char_whitelist=23456"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
    if not digit.isdigit():
        raise ValueError(f"unreadable seat count beside {' '.join(w['text'] for w in words)}")
    return int(digit)


def scan_name(text: str) -> str:
    tokens = [t for t in text.replace("*", " ").split() if t != "Ms."]
    initials = [t for t in tokens if re.fullmatch(r"[A-Z]\.", t)]
    surname = " ".join(t for t in tokens if t not in initials)
    return " ".join([*initials, surname])


def shepway(entry: dict) -> tuple[list[dict], list[dict]]:
    path = str(raw.cached(SOURCE, entry["file"]).path)
    rows: list[dict] = []
    wards: list[dict] = []
    year = 0
    errors: list[str] = []
    with tempfile.TemporaryDirectory() as folder:
        subprocess.run(
            ["pdftoppm", "-r", str(SCAN_DPI), "-gray", "-png", "-f", "3", "-l", "10", path, f"{folder}/page"],
            check=True,
            capture_output=True,
        )
        for image_path in sorted(Path(folder).glob("page-*.png")):
            page = int(image_path.stem.split("-")[1])
            image = Image.open(image_path).convert("L")
            size = image.size
            for left, right in SCAN_COLUMNS:
                state, heading, pending = "heading", [], ""
                for line in ocr_lines(image.crop((left, SCAN_ROWS[0], right, SCAN_ROWS[1])), left, SCAN_ROWS[0]):
                    text = " ".join(w["text"] for w in line)
                    title = re.search(r"Shepway - (\d{4})", text)
                    if title:
                        year = int(title[1])
                        state, heading = "heading", []
                        continue
                    if year not in SHEPWAY_DAYS:
                        continue
                    if state == "heading":
                        if re.search(r"vote share$", text):
                            joined = " ".join(w["text"] for w in heading)
                            at = max(i for i, w in enumerate(heading) if re.search(r"\(\d[\d,]*\)", w["text"]))
                            electorate = int(found(r"\((\d[\d,]*)\)", heading[at]["text"])[1].replace(",", ""))
                            name = joined[: joined.rindex("(" + found(r"\((\d[\d,]*)\)", heading[at]["text"])[1])]
                            ward = {
                                "name": JUNK.sub("", name).strip(),
                                "code": entry["code"],
                                "day": SHEPWAY_DAYS[year],
                                "registered": electorate,
                                "seats": superscript(image, heading[at:]),
                                "registered_box": fraction(heading[at]["box"], size),
                                "page": page,
                            }
                            wards.append(ward)
                            state = "candidates"
                        else:
                            heading += line
                        continue
                    turnout = re.search(r"Turnout ([\d.]+) (\S+)$", text)
                    if turnout:
                        state, heading = "heading", []
                        continue
                    text = re.sub(r" [|l]\.", " I.", JUNK.sub("", text))
                    match = re.fullmatch(
                        r"(.*?[A-Z]\.?(?: Ms\.)?\*?) ((?!Ms\.|\*)\D+?) =?\s*(\d[\d,]*) (\S+)",
                        re.sub(r"([a-z])([A-Z]\.)", r"\1 \2", text),
                    )
                    if match is None:
                        if re.fullmatch(r"[A-Za-z' -]+-", text):
                            pending = text
                            continue
                        errors.append(f"Shepway {year} page {page}: {text}")
                        continue
                    words_ = [w for w in line if any(c.isalpha() for c in w["text"])][: len(match[1].split())]
                    votes_word = next(w for w in line if w["text"].replace(",", "") == match[3].replace(",", ""))
                    rows.append(
                        candidate(
                            entry,
                            wards[-1],
                            page,
                            scan_name(pending + match[1]),
                            SCAN_PARTIES.get(match[2], match[2]),
                            int(match[3].replace(",", "")),
                            False,
                        )
                        | {"stroke": stroke(image, words_), "share": match[4], "box": fraction(votes_word["box"], size)}
                    )
                    pending = ""
    for ward in wards:
        lines_ = [row for row in rows if row["at"] is ward]
        seats = ward["seats"]
        if all(row["votes"] == 0 for row in lines_):
            if len(lines_) != seats:
                errors.append(f"Shepway {ward['day']} {ward['name']}: {len(lines_)} unopposed for {seats} seats")
            for row in lines_:
                row.update(votes=None, elected=True)
            continue
        shared = [row for row in lines_ if re.fullmatch(r"\d+\.\d", row["share"])]
        total = sum(row["votes"] for row in shared)
        for row in shared:
            if abs(100 * row["votes"] / total - float(row["share"])) > 0.051:
                errors.append(
                    f"Shepway {ward['day']} {ward['name']}: {row['name']} {row['votes']} is not {row['share']}%"
                )
        ranked = sorted(lines_, key=lambda row: -row["stroke"])
        bold, plain = ranked[:seats], ranked[seats:]
        if len(lines_) < seats or min(r["stroke"] for r in bold) - max((r["stroke"] for r in plain), default=0) < 0.2:
            errors.append(f"Shepway {ward['day']} {ward['name']}: bold names unclear")
        for row in bold:
            row["elected"] = True
    if errors:
        raise ValueError("\n".join(errors))
    for row in rows:
        del row["stroke"], row["share"]
    return rows, wards


def fraction(box: tuple[int, int, int, int], size: tuple[int, int]) -> list[float]:
    return [
        round(box[0] / size[0], 5),
        round(box[1] / size[1], 5),
        round(box[2] / size[0], 5),
        round(box[3] / size[1], 5),
    ]


def parse() -> None:
    rows: list[dict] = []
    wards: list[dict] = []
    for entry in FILES:
        if entry["parser"] == "shepway":
            continue
        parser = {"kent-2001": kent_2001, "kent-handbook": kent_handbook, "surrey": surrey}.get(entry["parser"], table)
        found_rows, found_wards = parser(entry)
        for ward in found_wards:
            ward.setdefault("day", entry["day"])
        rows += found_rows
        wards += found_wards
    for found_rows, found_wards in (
        calderdale(),
        leeds(),
        *(shepway(entry) for entry in FILES if entry["parser"] == "shepway"),
    ):
        rows += found_rows
        wards += found_wards
    for row in rows:
        ward = row.pop("at")
        row["name"] = FIXES.get(row["name"], re.sub(r"(\w)- (\w)", r"\1-\2", row["name"]))
        row.update(
            day=ward["day"],
            precision=ward.get("precision", "day"),
            by=ward.get("by", False),
            ward=ward["name"],
            box=ward.get("box"),
        )
    frame = pl.DataFrame(rows, infer_schema_length=None).with_row_index("number")
    figures = {(w["code"], w["day"], w["name"]): w for w in wards}
    extra = []
    for (code, day, ward), group in frame.group_by("code", "day", "ward", maintain_order=True):
        info = figures[(code, day, ward)]
        votes = group["votes"].drop_nulls()
        elected = int(group["elected"].sum())
        tacit = len(votes) == 0
        seats = info.get("seats", group.height if tacit else max(elected, 1))
        if len(votes) not in (0, group.height):
            raise ValueError(f"{code} {day} {ward}: votes for some candidates only")
        if tacit and (group.height != seats or elected != seats):
            raise ValueError(f"{code} {day} {ward}: {group.height} unopposed candidates for {seats} seats")
        if elected == 0 and code in (CALDERDALE, LEEDS):
            print(f"{code} {day} {ward}: no candidate marked elected")
        elif elected != seats:
            raise ValueError(f"{code} {day} {ward}: {elected} elected for {seats} seats")
        if not tacit and elected:
            floor = min(group.filter(pl.col("elected"))["votes"].to_list())
            if max(group.filter(~pl.col("elected"))["votes"].to_list(), default=0) > floor:
                print(f"{code} {day} {ward}: a candidate not elected outpolls one elected")
        registered, ballots, rejected = info.get("registered"), info.get("ballots"), info.get("rejected")
        over = None
        if registered and ballots and "percent" in info and abs(100 * ballots / registered - info["percent"]) > 0.02:
            raise ValueError(f"{code} {day} {ward}: {ballots}/{registered} is not {info['percent']}%")
        if ballots and seats == 1 and not tacit:
            counted = int(votes.sum()) + (rejected or 0)
            if code == CALDERDALE and counted != ballots:
                raise ValueError(f"{code} {day} {ward}: {counted} votes and rejected papers, {ballots} ballots")
            if counted > ballots:
                print(f"{code} {day} {ward}: {counted} votes and rejected papers, {ballots} ballots")
                over = counted
        extra.append(
            {
                "code": code,
                "day": day,
                "ward": ward,
                "seats": seats,
                "registered": registered,
                "ballots": ballots,
                "rejected": rejected,
                "over": over,
                "registered_box": info.get("registered_box"),
            }
        )
    frame = frame.join(pl.DataFrame(extra, infer_schema_length=None), on=["code", "day", "ward"], how="left")
    dover_1999 = Counter(
        next(
            (group for group in ("Labour", "Conservative", "Liberal Democrat") if group in row["description"]), "Other"
        )
        for row in frame.filter(
            (pl.col("code") == DOVER) & (pl.col("day") == date(1999, 5, 6)) & pl.col("elected")
        ).iter_rows(named=True)
    )
    if dover_1999 != {"Labour": 28, "Conservative": 26, "Liberal Democrat": 1, "Other": 1}:
        raise ValueError(f"Dover 1999 seats by party {dict(dover_1999)} differ from the composition table")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "candidates.parquet")
    for (code, day), group in frame.group_by("code", "day", maintain_order=True):
        print(
            f"{code} {day}: {group['ward'].n_unique()} wards, {group.height} candidates, {int(group['elected'].sum())} elected"
        )
    for description, count in Counter(frame["description"].to_list()).most_common():
        print(f"{count:4} {description}")


def ward_keys(frame: pl.DataFrame) -> dict[tuple[str, date, str], str]:
    regular = frame.filter(~pl.col("by")).select("code", "day", "ward").unique()
    keys = {}
    for code, day, ward, by in frame.select("code", "day", "ward", "by").unique().iter_rows():
        year = day.year
        if by:
            earlier = regular.filter((pl.col("code") == code) & (pl.col("ward") == ward) & (pl.col("day") <= day))
            if earlier.height:
                year = max(earlier["day"].to_list()).year
        keys[(code, day, ward)] = f"{code}:{slug(ward)}:{year}"
    return keys


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    keys = ward_keys(candidates)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="English council elections from 1965 to 2001 by ward, from council-published results in Dover, "
            "Kent, Epsom and Ewell, Surrey, Bedford, Calderdale, Shepway, and Leeds",
            polity="GB",
        )
        outranked = foreign_preferred(conn, cs, SOURCE)

        def name(entity: UUID, label: str) -> None:
            if entity in outranked and entity not in cs.minted:
                return
            cs.add("name", entity_id=entity, text=label, lang="en", kind="official", is_preferred=True)

        def caveat(entity: UUID, text: str) -> None:
            cs.add("note", entity_id=entity, kind="caveat", body_md=text)

        gb = cs.polity("GB")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("publish gb.umit and gb.parliament first")
        parties = {key: cs.key(*key) for key in set(PARTIES.values())}
        if cs.minted & set(parties.values()):
            raise ValueError("a party this pipeline links is not in the archive")

        sources: dict[str, UUID] = {}
        artifacts: dict[str, tuple[raw.Artifact, UUID]] = {}
        for entry in FILES:
            sources[entry["file"]] = cs.source(entry["publisher"], entry["title"])
            licensed = entry.get("license", "Open Government Licence v3.0") is not None
            cs.add(
                "source",
                id=sources[entry["file"]],
                kind=entry.get("kind", "official_body"),
                publisher=entry["publisher"],
                title=entry["title"],
                url=entry["file"],
                license="Open Government Licence v3.0" if licensed else None,
                license_url=OGL if licensed else None,
                attribution="Contains public sector information licensed under the Open Government Licence v3.0."
                if licensed
                else None,
                notes=entry["notes"],
            )
            artifact = raw.cached(SOURCE, entry["file"])
            artifacts[entry["file"]] = (artifact, cs.artifact(artifact, sources[entry["file"]]))
        page_ids: dict[tuple[str, int], UUID] = {}

        def page(file: str, number: int) -> UUID:
            if (file, number) not in page_ids:
                artifact, artifact_id = artifacts[file]
                image = PAGES / f"{artifact.sha256}-{number}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(artifact.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = Image.open(image).size
                page_ids[(file, number)] = cs.page(artifact_id, number)
                cs.add(
                    "source_page",
                    id=page_ids[(file, number)],
                    artifact_id=artifact_id,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
            return page_ids[(file, number)]

        def figure(result_set: UUID, unit: UUID, option: UUID | None, measure: int, value: int, line: dict, box):
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=DIGITIZED if box is not None else 0,
                value=value,
            )
            if box is None:
                return
            cs.add(
                "value_locator",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                page_id=page(line["file"], line["page"]),
                source_id=None,
                x0=box[0],
                y0=box[1],
                x1=box[2],
                y1=box[3],
            )

        calderdale_source = cs.source("Calderdale Council", "Election results, 1999 and 2000")
        cs.add(
            "source",
            id=calderdale_source,
            kind="official_body",
            publisher="Calderdale Council",
            title="Election results, 1999 and 2000",
            url=CALDERDALE_SITE,
            license="Open Government Licence v3.0",
            license_url=OGL,
            attribution="Contains public sector information licensed under the Open Government Licence v3.0.",
            notes=CALDERDALE_NOTES,
        )
        for url in candidates.filter(pl.col("code") == CALDERDALE)["file"].unique().sort():
            sources[url] = calderdale_source
            cs.artifact(raw.cached(SOURCE, url), calderdale_source)
        leeds_source = cs.source("Leeds City Council", "Leeds City Council election results, 1973 to 2000")
        cs.add(
            "source",
            id=leeds_source,
            kind="official_body",
            publisher="Leeds City Council",
            title="Leeds City Council election results, 1973 to 2000",
            url=LEEDS_SITE,
            license="Open Government Licence v3.0",
            license_url=OGL,
            attribution="Contains public sector information licensed under the Open Government Licence v3.0.",
            notes=LEEDS_NOTES,
        )
        for url in candidates.filter(pl.col("code") == LEEDS)["file"].unique().sort():
            sources[url] = leeds_source
            cs.artifact(raw.cached(SOURCE, url), leeds_source)

        councils: dict[str, tuple[UUID, UUID, UUID, str, str]] = {}
        for code in candidates["code"].unique(maintain_order=True):
            area = cs.area("gb.lad", code)
            polity = cs.key("gb.polity", code)
            body = cs.key("gb.body", code)
            if cs.minted & {polity, body}:
                raise ValueError(f"{code}: council not in the archive")
            path, council_name = conn.execute(
                "select (select path from slug where entity_id = %s order by is_current desc limit 1),"
                " (select text from name where entity_id = %s and is_preferred limit 1)",
                [polity, body],
            ).fetchall()[0]
            councils[code] = (area, polity, body, path, council_name)

        made: set[UUID] = set()
        made_wards: set[str] = set()
        for (code, day, by, precision), council_lines in candidates.group_by(
            "code", "day", "by", "precision", maintain_order=True
        ):
            area, polity, body, path, council_name = councils[code]
            stamp = f"{day:%Y-%m}" if precision == "month" else str(day)
            national = None
            if not by and precision == "day":
                national = cs.key("gb.election", f"{day}:local:gb")
                if national in cs.minted and national not in made:
                    made.add(national)
                    cs.add(
                        "election",
                        id=national,
                        polity_id=gb,
                        kind="local",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    wales = day.year in (1976, 1979, 1983, 1985, 1987, 1989, 1991, 1993, 1995)
                    label = f"{day.year} local elections in England{' and Wales' if wales else ''}"
                    name(national, LEEDS_NATIONAL.get(day, label))
                    cs.add("slug", path=f"gb/{day}-local", entity_id=national)
            election = cs.key("gb.election", f"{stamp}:local:{code}")
            if election in cs.minted:
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=national,
                    kind="by_election" if by else "local",
                    date_from=day,
                    date_to=day,
                    date_precision=precision,
                    status="held",
                )
                when = f"{day:%B %Y}" if precision == "month" else f"{day.day} {day:%B %Y}"
                name(election, f"{council_name} by-election, {when}" if by else f"{day.year} {council_name} election")
                cs.add("slug", path=f"{path}/{stamp}-local", entity_id=election)
                wards = council_lines["ward"].n_unique()
                if code == SURREY and not by:
                    caveat(
                        election,
                        f"Only the {wards} divisions in Epsom and Ewell are loaded, from the borough council's "
                        "history of the county elections; the county's other divisions are not in the archive yet.",
                    )
                if code == SHEPWAY:
                    caveat(
                        election,
                        "These results come from the Elections Centre's compilation of Shepway results, read from a "
                        "scan; for the early years it relied largely on local newspaper reports, not the council's "
                        "declarations.",
                    )
                if precision == "month" and not by:
                    caveat(election, f"The source gives the month of the poll, {day:%B %Y}, not the day.")
            noun = "division" if code[:3] == "E10" else "ward"
            for (ward,), lines in council_lines.group_by("ward", maintain_order=True):
                lines = lines.sort("number")
                first = lines.row(0, named=True)
                ward_key = keys[(code, day, ward)]
                ward_area = cs.area("gb.ward", ward_key)
                unit = cs.unit("gb.ward", ward_key)
                fresh = ward_key not in made_wards
                made_wards.add(ward_key)
                if ward_area in cs.minted and fresh:
                    cs.add("area", id=ward_area, scheme_code="gb.ward")
                    cs.add("area_code", area_id=ward_area, code=ward_key, valid_from=None, valid_to=None)
                    cs.add("area_parent", area_id=ward_area, parent_id=area, valid_from=None, valid_to=None)
                    name(ward_area, ward)
                if unit in cs.minted and fresh:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="gb.ward",
                        code=ward_key,
                        area_relation="equals",
                        area_id=ward_area,
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, ward)
                seats = first["seats"]
                tacit = lines["votes"].is_null().all()
                contest_key = f"{stamp}:local:{code}:{slug(ward)}{':by' if by else ''}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=ward_area,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=systems["fptp" if seats == 1 else "block"],
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{ward} {noun}, {council_name}, {day.year}")
                cs.add("slug", path=f"{path}/{stamp}-local/{slug(ward)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                source = sources[first["file"]]
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
                locator: dict[str, object] = {"ward": ward}
                if first["page"]:
                    locator["page"] = first["page"]
                    if first["box"] is not None:
                        locator["bbox"] = [value / 1000 for value in first["box"]]
                else:
                    locator["url"] = first["file"]
                cs.add("citation", entity_id=result_set, source_id=source, locator=locator)
                if not tacit and not lines["elected"].any():
                    caveat(result_set, "The council's page marks no candidate elected, so no winner is recorded.")
                if first["over"] is not None:
                    caveat(
                        result_set,
                        f"The declaration's votes and rejected papers add up to {first['over']:,}, more than the "
                        f"{first['ballots']:,} ballot papers it says were issued; both are stored as printed.",
                    )
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    description = line["description"]
                    party = parties[PARTIES[description]] if description in PARTIES else None
                    label = line["name"]
                    if description is not None and party is None and description not in INDEPENDENT:
                        caveat(result_set, f"{label} stood as “{description}”; no party is linked.")
                    option = cs.key(f"{SOURCE}.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=label,
                    )
                    person = cs.key(f"{SOURCE}.person", option_key)
                    cs.add("person", id=person)
                    name(person, label)
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=label,
                        member_of_party_id=party,
                        is_independent=description in INDEPENDENT,
                    )
                    if not tacit:
                        figure(result_set, unit, option, VOTES, line["votes"], line, line.get("box"))
                    if line["elected"] or tacit:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
                if tacit:
                    continue
                elected = lines.filter(pl.col("elected"))
                if elected.height:
                    floor = elected.sort("votes").row(0, named=True)
                    above = lines.filter(~pl.col("elected") & (pl.col("votes") > floor["votes"]))
                    for line in above.iter_rows(named=True):
                        caveat(
                            result_set,
                            f"The source marks {floor['name']} elected with {floor['votes']:,} votes and "
                            f"{line['name']} not elected with {line['votes']:,}; both are shown as published.",
                        )
                for measure, field in ((REGISTERED, "registered"), (BALLOTS, "ballots"), (INVALID, "rejected")):
                    if first[field] is None:
                        continue
                    box = first.get("registered_box") if measure == REGISTERED else None
                    figure(result_set, unit, None, measure, first[field], first, box)
        return cs.write()
