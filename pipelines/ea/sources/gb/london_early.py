import base64
import difflib
import io
import json
import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.names import foreign_preferred
from ea.sources.gb import leh
from ea.sources.gb import london_boroughs as lb
from ea.store import DATA, connect

SOURCE = "gb.london_early"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DOWNLOAD = lb.DOWNLOAD
PUBLISHER = "Greater London Council"
MODEL = "claude-haiku-5-5"
PROMPT = "1"
LONGEST = 1568
READS = {1982: (f"{DOWNLOAD}/575f3aa9-8b89-4096-a7bb-ffc893bcfff9/LBCE_1982-5-6.pdf", (7, 68))}
READ_SYSTEM = (
    "You read one page of Table 1, Election results by ward, from the Greater London Council's report on the London "
    "borough council elections of 6 May 1982. The page has two columns: read the left column from top to bottom, "
    "then the right column. Return every heading and candidate line in that order. A borough heading is a borough "
    "name in capitals, often boxed, or a line such as CROYDON - continued at the top of a column. A ward heading is "
    "a ward name in capitals above its candidates. A candidate line has the surname, forenames or initials, the "
    "party code, and the votes. Copy text exactly as printed, including punctuation in names. Give the party code "
    "exactly as printed (such as CON, LAB, LIBSDP, SDPLIB, or LABCP) and the votes as printed with their commas, or "
    "the word printed instead of a figure (such as Unopposed). Say whether each candidate's name is indented "
    "relative to the first candidate of the ward: the elected are flush left and the others are indented. Skip the "
    "table title, the running footer, and the page number."
)
READ_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "rows": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "enum": ["borough", "ward", "candidate"]},
                    "text": {"type": "string"},
                    "party": {"type": ["string", "null"]},
                    "votes": {"type": ["string", "null"]},
                    "indented": {"type": ["boolean", "null"]},
                },
                "required": ["kind", "text", "party", "votes", "indented"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["rows"],
    "additionalProperties": False,
}
INDEPENDENT = {"Independent", "Citizen", "Electrician", "Labourer", "Theatrical Agent", "Retired Garage Proprietor"}
METHODOLOGY = (
    "Read from the text layer of the Greater London Council's scanned report, with a second reading by optical "
    "character recognition of every column and the page images checked where the two differ. The report lists each "
    "ward's candidates in vote order and prints the elected in bold. Every party's London-wide candidates and votes "
    "add up to the report's table of candidates and votes by party, each borough's elected councillors by party "
    "match its table of the state of the parties in the new councils, and each ward's electorate comes from its "
    "summary of results by wards, whose ward figures add up to each borough's printed total. Party labels marked in "
    "the report as Labour and Co-operative or as local associations are stored under the party code the report "
    "counts them in. Candidate names are printed as surname and forenames or initials."
)
NOTES = {
    1964: "The report's results pages omit West Hill ward, Wandsworth, which elected three councillors from 12,015 "
    "electors, so its contest is not recorded. The results pages label the three councillors elected for Upton ward, "
    "Newham, as Liberals, while the state of the parties counts no Liberals in Newham and ten Independents; in "
    "Croydon they give the Conservatives 24 councillors where the state of the parties prints 21, and the R and "
    "Con/R candidates 15 where it prints 18 Independents; in Merton, 24 Conservatives and 4 R against 25 and 3; and "
    "in Hammersmith, 6 Conservatives and 54 Labour against 7 and 53. The labels are stored as the results pages "
    "give them. In 17 wards the candidates' votes exceed what the scanned table's number voting allows, and in "
    "Chislehurst, Bromley, and Beddington South, Sutton, a party's candidates are not listed in vote order; the "
    "votes are stored as printed. Newham's ward electorates add up to 179,960, 90 more than its printed total of "
    "179,870. Mrs P. J. L. Holroyde, a Liberal candidate in Hampton ward, Richmond upon Thames, is footnoted as "
    "convicted of shoplifting during the campaign and as having said she would resign if elected.",
    1968: "Thirty seats were filled unopposed and are stored without votes. The report prints the votes of Clissold "
    "and Defoe wards, Hackney, as corrected after an examination ordered by the County Court found a uniform error "
    "of 500 votes for every candidate polling over 500, which did not change the result; its summary by wards gives "
    "party shares for those wards that do not fit the corrected figures. The Craig Park and Highfield votes in "
    "Enfield are those revised after an inspection and recount on 7 November 1968 ordered by the High Court. Six "
    "Croydon candidates in Norbury and West Thornton, printed as Conservative and Ratepayers, are counted with the "
    "Ratepayers and Residents Associations in the party table and with the Conservatives in the state of the "
    "parties; this record keeps the printed label. The summary by wards swaps the party shares of Sanderstead & "
    "Selsdon and Sanderstead North, Croydon, and gives Labour 11.1 percent in St. Leonard's ward, Lambeth, where "
    "the printed votes give 15.7 percent; the votes are stored as printed. In Uxbridge ward, Hillingdon, the report "
    "prints R. Pinkney's 449 votes above A. Schaverien's 525, and that order is kept. Candidates, elected "
    "councillors by party, and ward electorates tie to the printed tables, yet the party table's vote totals "
    "differ from the candidates' sums: 3,588 more for Labour, 1,301 more for the Conservatives, 508 more for the "
    "Ratepayers and Residents Associations, and 3,512 fewer for the Liberals, 402 for the Independents, and 105 "
    "for the Communists; the candidates' figures are stored as printed.",
    1974: "The report prints no vote figure for D. J. Harvey, elected for the Conservatives in Chamberlayne ward, "
    "Brent, so his candidacy and election are stored without a vote count; the party table's Conservative total "
    "exceeds the sum of the printed Conservative votes by 971, which includes his. The Childs Hill ward votes in "
    "Barnet are those revised after an examination of the ballot papers ordered by the County Court on 9 May 1974, "
    "after which A. Young was declared elected in place of Mrs M. T. Catterson. The report's summary by wards gives "
    "the Liberals 5.1 percent in Ravensbury ward, Merton, where the printed votes give 5.7 percent; the votes are "
    "stored as printed.",
    1971: "Both readings of the report agree on every candidate's votes. The report prints 2,553 votes for "
    "J. D. Gordon-Lee, elected for the Conservatives in Hendon ward, Barnet, where its summary by wards gives party "
    "shares that fit 2,353; with that, the party table's Conservative total exceeds the sum of the printed "
    "Conservative votes by 209 and its Labour total falls 10 short of the printed Labour votes; the candidates' "
    "figures are stored as printed. The third letter of N. K. Dewey's surname, a Conservative candidate in "
    "Bow South ward, Tower Hamlets, is blotted in the report and is read as w. Five candidate lines missing from "
    "the report's text layer were read from the page images.",
    1978: "Both readings of the report agree on every candidate's votes, and each ward's party shares match the "
    "report's summary by wards, yet the party table's totals differ from the candidates' sums by 153 votes for "
    "Labour (short), 121 for the Conservatives (over), and 38 for the Liberals (short); the candidates' figures "
    "are stored as printed. The report prints Richard W. Gregory twice in South Defoe ward with 367 votes; the "
    "party table and the ward's shares count him once, and so does this record.",
    1986: "The London Residuary Body's Research and Intelligence Unit compiled this report after the Greater London "
    "Council was abolished, so the text layer and the second reading were checked against each borough's seats in "
    "the summary by wards and against the party table's candidates, votes, and elected instead of a state-of-the-"
    "parties table. The party table counts Kishore K. Gumaste, printed in Wallington North as SDP (Alliance), among "
    "the Liberal (Alliance) candidates; this record keeps the ward table's label. In Blackfen ward, William J. Boyd "
    "and Peter W. Shonk tied on 1,072 votes for the second seat and Boyd was elected by lot. The Wallington North "
    "poll was deferred to 19 June 1986.",
}
METHODOLOGY_1964 = (
    "Read from the London County Council's report as the London Datastore publishes it: its scanned tables give "
    "each ward's councillors to be elected, electors, and number voting, and its results pages, a later typed "
    "transcription, give each ward's candidates in vote order with party, votes, and the elected in bold. Each "
    "borough's ward electorates add up to its printed total, each ward's elected candidates number its councillors "
    "to be elected, and each borough's elected councillors by party match the report's table of the state of the "
    "parties in the new councils except where the note says otherwise. The report prints no table of candidates and "
    "votes by party and no key to its party codes; it counts the R (Ratepayers and Residents Associations) and Con/R "
    "candidates it elected among Independents. Candidate names are printed as initials or forenames before the "
    "surname."
)
POSTPONED = (
    "The poll in this ward was countermanded after the death of a candidate and held later, as the report notes."
)
BOOKS: dict[int, dict] = {
    1964: {
        "day": date(1964, 5, 7),
        "publisher": "London County Council",
        "methodology": METHODOLOGY_1964,
        "url": f"{DOWNLOAD}/6139a24b-b165-4790-8ae5-7896f9b8e08f/LBCE_1964-5-7.pdf",
        "candidates": (13, 62),
        "wards": (3, 9),
        "seats": True,
        "forenames": True,
        "titled": True,
        "figures": {(5, "10059"): "10,059", (6, "15.728"): "15,728"},
        "electorate_residual": {"Newham": -90},
        "absent": {("Wandsworth", "West Hill")},
        "names": {
            "pymmes": "Pymmes",
            "Arkley East.": "Arkley East",
            "Winch more Hill": "Winchmore Hill",
            "Col ham-Cowley": "Colham-Cowley",
            "HounslowWest": "Hounslow West",
            "Clerkenwell.": "Clerkenwell",
            "Red cliffe": "Redcliffe",
            "Blackheath and Lewisham": "Blackheath and Lewisham Village",
            "Little IIford": "Little Ilford",
            "IIford": "Ilford",
            "High Street.": "High Street",
            "Coning ham": "Coningham",
            "Starch Green I": "Starch Green",
        },
        "columns": ["Con", "Lab", "Lib", "Ind", "Comm"],
        "merged": {"R": "Ind", "Con/R": "Ind"},
        "offsets": {
            "Croydon": {"Con": -3, "Ind": 3},
            "Hammersmith": {"Con": 1, "Lab": -1},
            "Merton": {"Con": 1, "Ind": -1},
            "Newham": {"Lib": -3, "Ind": 3},
        },
        "state": {
            "Barking": {"Lab": 45, "Ind": 4, "Total": 49},
            "Barnet": {"Con": 37, "Lab": 13, "Lib": 6, "Total": 56},
            "Bexley": {"Con": 17, "Lab": 39, "Total": 56},
            "Brent": {"Con": 26, "Lab": 34, "Total": 60},
            "Bromley": {"Con": 38, "Lab": 15, "Lib": 7, "Total": 60},
            "Camden": {"Con": 26, "Lab": 34, "Total": 60},
            "Croydon": {"Con": 21, "Lab": 21, "Ind": 18, "Total": 60},
            "Ealing": {"Con": 26, "Lab": 34, "Total": 60},
            "Enfield": {"Con": 29, "Lab": 31, "Total": 60},
            "Greenwich": {"Con": 11, "Lab": 49, "Total": 60},
            "Hackney": {"Lab": 60, "Total": 60},
            "Hammersmith": {"Con": 7, "Lab": 53, "Total": 60},
            "Haringey": {"Con": 19, "Lab": 41, "Total": 60},
            "Harrow": {"Con": 36, "Lab": 20, "Total": 56},
            "Havering": {"Con": 16, "Lab": 27, "Ind": 12, "Total": 55},
            "Hillingdon": {"Con": 24, "Lab": 36, "Total": 60},
            "Hounslow": {"Con": 12, "Lab": 48, "Total": 60},
            "Islington": {"Lab": 60, "Total": 60},
            "Kensington and Chelsea": {"Con": 46, "Lab": 14, "Total": 60},
            "Kingston upon Thames": {"Con": 40, "Lab": 20, "Total": 60},
            "Lambeth": {"Con": 18, "Lab": 42, "Total": 60},
            "Lewisham": {"Con": 15, "Lab": 45, "Total": 60},
            "Merton": {"Con": 25, "Lab": 26, "Ind": 3, "Total": 54},
            "Newham": {"Lab": 50, "Ind": 10, "Total": 60},
            "Redbridge": {"Con": 45, "Lab": 15, "Total": 60},
            "Richmond upon Thames": {"Con": 41, "Lab": 12, "Ind": 1, "Total": 54},
            "Southwark": {"Con": 6, "Lab": 54, "Total": 60},
            "Sutton": {"Con": 30, "Lab": 17, "Ind": 4, "Total": 51},
            "Tower Hamlets": {"Lab": 55, "Ind": 2, "Comm": 3, "Total": 60},
            "Waltham Forest": {"Con": 3, "Lab": 36, "Ind": 9, "Total": 48},
            "Wandsworth": {"Con": 13, "Lab": 47, "Total": 60},
            "Westminster": {"Con": 41, "Lab": 19, "Total": 60},
        },
        "parties": {
            "Lab": ("Labour", None, None),
            "Con": ("Conservative", None, None),
            "Lib": ("Liberal", None, None),
            "Comm": ("Communist", None, None),
            "R": ("Ratepayers and Residents Associations", None, None),
            "New Lib": ("New Liberal", None, None),
            "Con/R": ("Conservative and Ratepayers", None, None),
            "BNP": ("British National Party (1960)", None, None),
            "UM": ("Union Movement", None, None),
            "Ind Lab": ("Independent Labour", None, None),
            "Ind Con": ("Independent Conservative", None, None),
            "Ind Lib": ("Independent Liberal", None, None),
            "Ind Comm": ("Independent Communist", None, None),
            "Rad Lib": ("Radical Liberal", None, None),
            "ILP": ("Independent Labour Party", None, None),
            "Ind P/Lab": ("Ind P/Lab", None, None),
            "Ind": ("Independent", None, None),
        },
    },
    1968: {
        "day": date(1968, 5, 9),
        "url": f"{DOWNLOAD}/05a66a06-d5a5-4b50-9318-1087d2e32d67/LBCE_1968-5-9.pdf",
        "candidates": (7, 57),
        "wards": (58, 72),
        "split": (58, 72),
        "plain": True,
        "trailing": True,
        "titled": True,
        "figures": {
            (58, "c"): None,
            (31, "HiHingdon"): "Hillingdon",
            (67, "MERTaN"): "MERTON",
            (67, "ERTaN"): "ERTON",
            (7, "/554"): "554",
            (11, "6t4"): "614",
            (23, "28ffi"): "2805",
            (23, "1ffi4"): "1054",
            (24, "6.97"): "697",
            (26, "8(Xj"): "805",
            (27, "16119"): "1619",
            (51, "135,"): "135",
            (46, "220'1"): "2207",
        },
        "headings": {
            "HARNET": "Barnet",
            "HlLLINGDON": "Hillingdon",
            "HILLINGOON": "Hillingdon",
            "REDBRlDGE": "Redbridge",
            "REDBRIIX}E": "Redbridge",
            "WALTHAM": "Waltham Forest",
            "WALTHAM FORE5T": "Waltham Forest",
            "CHELSEA (Royal Borough)": "Kensington and Chelsea",
            "THAMES (Royal Borough)": "Kingston upon Thames",
            "THAMES": "Richmond upon Thames",
            "WESTMINSTER CITY": "Westminster, City of",
        },
        "names": {
            "L ongbridge": "Longbridge",
            "st _Paul's": "St. Paul's",
            "st. Mary's": "St. Mary's",
            "st. Michael's": "St. Michael's",
            "st. Mary Cray": "St. Mary Cray",
            "st. Paul's Cray": "St. Paul's Cray",
            "st. John's": "St. John's",
            "st. Pancras": "St. Pancras",
            "Norbury.": "Norbury",
            "WaxlowManor": "Waxlow Manor",
            "pymmes": "Pymmes",
            "st. Alphege": "St. Alphege",
            "st. Peter's": "St. Peter's",
            "Silver street": "Silver Street",
            "st. George's": "St. George's",
            "st. Margaret's": "St. Margaret's",
            "st. Nicholas": "St. Nicholas",
            "CoTham-Cowley": "Colham-Cowley",
            "st. Mary": "St. Mary",
            "St, Leonard's": "St. Leonard's",
            "stockwell": "Stockwell",
            "streatham South": "Streatham South",
            "streatham Wells": "Streatham Wells",
            "VassaIl": "Vassall",
            "L adywell": "Ladywell",
            "L ewisham Park": "Lewisham Park",
            "st Andrew": "St. Andrew",
            "st Mildred Lee": "St. Mildred Lee",
            "NewTown": "New Town",
            "College Park & Old": "College Park and Old Oak",
            "St. Katherine's": "St. Katharine's",
        },
        "aliases": {
            "Can": "Con",
            "Iud": "Ind",
            "Carom": "Comm",
            "Comm,": "Comm",
            "Ind Can": "Ind Con",
            "ColI": "Coll",
            "IT & R*": "R",
            "R & R*": "R",
            "R&R*": "R",
            "SRA*": "R",
            "WR*": "R",
            "IndR": "R",
            "Ind R": "R",
        },
        "residual": {"Con": -1301, "Lab": -3588, "Lib": 3512, "Comm": 105, "R": -508, "Ind": 402},
        "tallied": {
            name: "R"
            for name in ("Gower W.E.R.", "Willis R. G.", "Levy G.", "Tilbury R.", "Whitehorn E. F.A.", "Fowler E.J.")
        },
        "columns": ["Con", "Lab", "Lib", "Other"],
        "merged": {
            "Con/R": "Con",
            **{
                code: "Other"
                for code in (
                    "Comm",
                    "R",
                    "NF",
                    "UM",
                    "Ind Lab",
                    "Ind Con",
                    "SPGB",
                    "Lam",
                    "Tenants Ass.",
                    "Coll",
                    "Ind",
                )
            },
        },
        "state": {
            "Barking": {"Con": 13, "Lab": 32, "Other": 4, "Total": 49},
            "Barnet": {"Con": 56, "Lab": 3, "Lib": 1, "Total": 60},
            "Bexley": {"Con": 55, "Other": 1, "Total": 56},
            "Brent": {"Con": 49, "Lab": 11, "Total": 60},
            "Bromley": {"Con": 52, "Lab": 5, "Lib": 3, "Total": 60},
            "Camden": {"Con": 42, "Lab": 18, "Total": 60},
            "Croydon": {"Con": 47, "Lab": 1, "Lib": 1, "Other": 11, "Total": 60},
            "Ealing": {"Con": 53, "Lab": 5, "Other": 2, "Total": 60},
            "Enfield": {"Con": 51, "Lab": 9, "Total": 60},
            "Greenwich": {"Con": 38, "Lab": 22, "Total": 60},
            "Hackney": {"Con": 31, "Lab": 27, "Lib": 2, "Total": 60},
            "Hammersmith": {"Con": 54, "Lab": 6, "Total": 60},
            "Haringey": {"Con": 53, "Lab": 7, "Total": 60},
            "Harrow": {"Con": 56, "Total": 56},
            "Havering": {"Con": 35, "Lab": 7, "Other": 13, "Total": 55},
            "Hillingdon": {"Con": 60, "Total": 60},
            "Hounslow": {"Con": 53, "Lab": 7, "Total": 60},
            "Islington": {"Con": 47, "Lab": 10, "Other": 3, "Total": 60},
            "Kensington and Chelsea": {"Con": 57, "Lab": 3, "Total": 60},
            "Kingston upon Thames": {"Con": 59, "Lab": 1, "Total": 60},
            "Lambeth": {"Con": 57, "Lab": 3, "Total": 60},
            "Lewisham": {"Con": 44, "Lab": 16, "Total": 60},
            "Merton": {"Con": 46, "Lab": 4, "Other": 4, "Total": 54},
            "Newham": {"Con": 6, "Lab": 30, "Lib": 3, "Other": 21, "Total": 60},
            "Redbridge": {"Con": 55, "Lab": 5, "Total": 60},
            "Richmond upon Thames": {"Con": 54, "Total": 54},
            "Southwark": {"Con": 27, "Lab": 33, "Total": 60},
            "Sutton": {"Con": 41, "Lab": 7, "Other": 3, "Total": 51},
            "Tower Hamlets": {"Lab": 57, "Other": 3, "Total": 60},
            "Waltham Forest": {"Con": 44, "Lab": 4, "Total": 48},
            "Wandsworth": {"Con": 48, "Lab": 12, "Total": 60},
            "Westminster, City of": {"Con": 55, "Lab": 5, "Total": 60},
        },
        "parties": {
            "Con": ("Conservative", 1726, 3198468),
            "Con/R": ("Conservative and Ratepayers", 0, 0),
            "Lab": ("Labour", 1844, 1499480),
            "Lib": ("Liberal", 794, 387181),
            "Comm": ("Communist", 223, 34034),
            "R": ("Ratepayers and Residents Associations", 127, 157684),
            "NF": ("National Front", 14, 3382),
            "UM": ("Union Movement", 8, 1267),
            "Ind Lab": ("Independent Labour", 4, 859),
            "Ind Con": ("Independent Conservative", 3, 366),
            "SPGB": ("Socialist Party of Great Britain", 3, 150),
            "Lam": ("Lambethan", 2, 355),
            "Tenants Ass.": ("Tenants Association", 1, 58),
            "Coll": ("Hornsey College of Art", 1, 69),
            "Ind": ("Independent", 71, 47102),
        },
    },
    1971: {
        "day": date(1971, 5, 13),
        "url": f"{DOWNLOAD}/1871bfe0-e894-484e-bb07-66740b35e0f2/LBCE_1971-5-13.pdf",
        "candidates": (12, 65),
        "wards": (67, 99),
        "step": 2,
        "plain": True,
        "trailing": True,
        "titled": True,
        "figures": {
            (25, "~117"): "2117",
            (73, "10.026"): "10,026",
            (73, "10.334"): "10,334",
            (73, "10,760'"): "10,760",
            (81, "H1LLINGDON"): "HILLINGDON",
            (61, "lllGHAMlllLL"): "HIGHAM HILL",
            (61, "lllGHSTREET"): "HIGH STREET",
            (91, "II,051"): "11,051",
            (75, "5404"): "54.4",
            (75, "6404"): "64.4",
            (75, "4704"): "47.4",
            (75, "5904"): "59.4",
            (91, "13;046"): "13,046",
            (95, "4.229"): "4,229",
            (95, "6.576"): "6,576",
        },
        "missing": {
            13: [("Bignell", ("Turbutt C.", "Con", 471, False, [48, 748, 259, 758]))],
            44: [("Webb W. J.", ("Haynes R. V.", "Con", 1433, False, [326, 690, 525, 700]))],
            53: [("Carradice", ("Reynolds T. R.", "Lab", 2435, True, [47, 752, 259, 762]))],
            54: [
                ("Odam", ("Jackson H. J.", "Con", 1432, True, [45, 160, 258, 170])),
                ("Jakobi S. E", ("Schaler H. Miss", "Lab", 814, False, [323, 672, 525, 682])),
            ],
        },
        "residual": {"Con": 209, "Lab": -10},
        "elected": {
            (35, "Childerhouse W. A."): False,
            (35, "Bigrave W. T."): False,
            (35, "Russell R. A."): False,
            (35, "Dudmish P. E."): False,
            (35, "Whitmore O. M. Mrs"): False,
            (35, "Williams P. N. P."): False,
            (56, "Howley K. Mrs"): False,
            (56, "Holland J. Mrs"): False,
            (56, "Willoughby J. Mrs"): False,
            (56, "Cobley G.Mrs"): False,
            (56, "RuddellJ.T."): False,
        },
        "names": {
            "Crayford West J": "Crayford West",
            "St. Mary's l": "St. Mary's",
            "S t. Michael's": "St. Michael's",
            "Stoneb ridge": "Stonebridge",
            "Plaistow & Sundridge I": "Plaistow & Sundridge",
            "Priory J": "Priory",
            "St. Pancras J": "St. Pancras",
            "~ Norbury": "Norbury",
            "Waddon J": "Waddon",
            "Whitehorse Manor J": "Whitehorse Manor",
            "Woodside J": "Woodside",
            "West j": "West",
            "Well Hall j": "Well Hall",
            "Cen tral Homsey": "Central Hornsey",
            "South Homsey": "South Hornsey",
            "Wealds tone South": "Wealdstone South",
            "Colham - Cowley": "Colham-Cowley",
            "Station J": "Station",
            "Sou th Stanley": "South Stanley",
            "Hook and Southborough l": "Hook and Southborough",
            "Sydenham West J": "Sydenham West",
            "Whitefoot J": "Whitefoot",
            "Wimbledon West J": "Wimbledon West",
            "Manor Park I (": "Manor Park",
            "Woodgrange J": "Woodgrange",
            "llford": "Ilford",
            "Wan stead": "Wanstead",
            "Cen tral Twickenham": "Central Twickenham",
            "f Dockyard": "Dockyard",
            "St. Giles j": "St. Giles",
            "I Bow North": "Bow North",
            "Ley tons tone": "Leytonstone",
            "James Street": "St. James Street",
            "John": "St. John",
            "Mary's Park": "St. Mary's Park",
            "Southfield },putn,y": "Southfield",
            "'. Baker Street": "Baker Street",
            "Regent's Park J": "Regent's Park",
            "Capers Cope": "Copers Cope",
            "Coddington": "Goddington",
            "Elthome": "Elthorne",
            "Amos": "Arnos",
            "North": "Feltham North",
        },
        "columns": ["Con", "Lab", "Lib", "Other"],
        "merged": {
            code: "Other"
            for code in (
                "Comm",
                "R",
                "NF",
                "Ind R",
                "LIA",
                "HBR",
                "UM",
                "Dwarf",
                "HYM",
                "SPGB",
                "Ass Ind",
                "Ind Lab",
                "NDc",
                "SEP",
                "Ind Con",
                "Rep Ass",
                "Citizen",
                "C+HR",
                "FP",
                "Ind FL",
                "Indian Ind",
                "LS",
                "Local Ind",
                "ND",
                "NHBF",
                "NRHI",
                "PD",
                "SE Ind",
                "TC",
                "TV",
                "U-Con",
                "Ind",
            )
        },
        "state": {
            "Barking": {"Lab": 45, "Other": 4, "Total": 49},
            "Barnet": {"Con": 43, "Lab": 17, "Total": 60},
            "Bexley": {"Con": 24, "Lab": 32, "Total": 56},
            "Brent": {"Con": 22, "Lab": 38, "Total": 60},
            "Bromley": {"Con": 41, "Lab": 15, "Lib": 4, "Total": 60},
            "Camden": {"Con": 11, "Lab": 49, "Total": 60},
            "Croydon": {"Con": 30, "Lab": 27, "Other": 3, "Total": 60},
            "Ealing": {"Con": 20, "Lab": 40, "Total": 60},
            "Enfield": {"Con": 32, "Lab": 28, "Total": 60},
            "Greenwich": {"Con": 5, "Lab": 55, "Total": 60},
            "Hackney": {"Lab": 60, "Total": 60},
            "Hammersmith": {"Con": 2, "Lab": 58, "Total": 60},
            "Haringey": {"Con": 19, "Lab": 41, "Total": 60},
            "Harrow": {"Con": 26, "Lab": 27, "Lib": 2, "Other": 1, "Total": 56},
            "Havering": {"Con": 13, "Lab": 30, "Other": 12, "Total": 55},
            "Hillingdon": {"Con": 24, "Lab": 36, "Total": 60},
            "Hounslow": {"Con": 13, "Lab": 47, "Total": 60},
            "Islington": {"Lab": 60, "Total": 60},
            "Kensington and Chelsea": {"Con": 39, "Lab": 21, "Total": 60},
            "Kingston upon Thames": {"Con": 40, "Lab": 20, "Total": 60},
            "Lambeth": {"Con": 9, "Lab": 51, "Total": 60},
            "Lewisham": {"Con": 4, "Lab": 56, "Total": 60},
            "Merton": {"Con": 21, "Lab": 29, "Other": 4, "Total": 54},
            "Newham": {"Lab": 53, "Other": 7, "Total": 60},
            "Redbridge": {"Con": 42, "Lab": 18, "Total": 60},
            "Richmond upon Thames": {"Con": 37, "Lab": 14, "Lib": 3, "Total": 54},
            "Southwark": {"Con": 2, "Lab": 58, "Total": 60},
            "Sutton": {"Con": 25, "Lab": 21, "Other": 5, "Total": 51},
            "Tower Hamlets": {"Lab": 60, "Total": 60},
            "Waltham Forest": {"Con": 9, "Lab": 39, "Total": 48},
            "Wandsworth": {"Con": 7, "Lab": 53, "Total": 60},
            "Westminster, City of": {"Con": 37, "Lab": 23, "Total": 60},
        },
        "parties": {
            "Lab": ("Labour", 1861, 3184710),
            "Con": ("Conservative", 1745, 2363299),
            "Lib": ("Liberal", 595, 253255),
            "Comm": ("Communist", 140, 20212),
            "R": ("Ratepayers and Residents Associations", 110, 94421),
            "NF": ("National Front", 45, 4954),
            "Ind R": ("Independent Ratepayers", 31, 39718),
            "LIA": ("Liberal and Independent Alliance", 10, 2633),
            "HBR": ("Homes Before Roads", 6, 2093),
            "UM": ("Union Movement", 6, 571),
            "Dwarf": ("Dwarf", 4, 344),
            "HYM": ("Harrow Youth Movement", 4, 491),
            "SPGB": ("Socialist Party of Great Britain", 4, 137),
            "Ass Ind": ("Association of Independents", 3, 226),
            "Ind Lab": ("Independent Labour", 3, 668),
            "NDc": ("National Democratic", 3, 294),
            "SEP": ("Stop 11 Plus", 3, 628),
            "Ind Con": ("Independent Conservative", 2, 1504),
            "Rep Ass": ("Republican's Association", 2, 265),
            "Citizen": ("Citizen", 1, 105),
            "C+HR": ("Civil and Human Rights", 1, 67),
            "FP": ("Fellowship Party", 1, 450),
            "Ind FL": ("Independent Federation of the Left", 1, 66),
            "Indian Ind": ("Indian Independent", 1, 27),
            "LS": ("Libertarian Socialist", 1, 130),
            "Local Ind": ("Local Independent", 1, 1585),
            "ND": ("National Democrat", 1, 167),
            "NHBF": ("Nationalist House Britons First", 1, 133),
            "NRHI": ("No Rates on Home Improvement", 1, 251),
            "PD": ("People's Democracy", 1, 124),
            "SE Ind": ("Self Employed Independent", 1, 270),
            "TC": ("Tenant's Candidate", 1, 398),
            "TV": ("A Tenant's Voice", 1, 205),
            "U-Con": ("Unofficial Conservative", 1, 567),
            "Ind": ("Independent", 43, 21427),
        },
    },
    1974: {
        "day": date(1974, 5, 2),
        "url": f"{DOWNLOAD}/f6b33f95-fca4-4f94-a15e-f0ffc946ed47/LBCE_1974-5-2.pdf",
        "candidates": (12, 71),
        "wards": (72, 105),
        "figures": {
            (72, "1Hi,294"): "119,294",
            (76, "197288"): "197,288",
            (76, ".8,371"): "8,371",
            (80, "G~EENWICH"): "GREENWICH",
            (82, "3,7.68"): "3,768",
            (13, "2,P5"): "2,175",
            (13, ".1,482"): "1,482",
            (18, "1409"): "1,409",
            (26, "1;733"): "1,733",
            (26, "1,7.1~"): "1,716",
            (27, "1,29~"): "1,295",
            (28, "719-"): "719",
            (38, "2,085>"): "2,085",
            (39, '"Lit'): "964",
            (39, "1,6n"): "1,677",
            (40, ",zt,722"): "2,722",
            (45, "7n"): "777",
            (51, ",1,707"): "1,707",
            (56, "1,1n"): "1,177",
            (61, "-319"): "319",
            (62, "·194"): "194",
            (66, "1,9n"): "1,977",
            (68, ",698"): "1,698",
            (32, "195"): "105",
            (40, "~60"): "560",
            (30, "/"): None,
            (62, "\"'-.."): None,
        },
        "missing": {17: [("Cohen,J.", ("Harvey, D. J.", "Con", None, True))]},
        "residual": {"Con": -971},
        "initials": True,
        "names": {
            "East F inchley": "East Finchley",
            "Mill Hill J": "Mill Hill",
            "Woodhouse )": "Woodhouse",
            "Cllrlton": "Carlton",
            "Ward CAMDEN-conti nued Holborn": "Holborn",
            "St. John's J": "St. John's",
            "West End J": "West End",
            "Add iscornbe": "Addiscombe",
            "Bensharn Manor": "Bensham Manor",
            "Waddon ]": "Waddon",
            "Woodcote & Coulsdon West J": "Woodcote & Coulsdon West",
            "Ward EALI NG-conti nued Perivale": "Perivale",
            "Bush H ill South": "Bush Hill South",
            "Town Hall ]": "Town Hall",
            "Harrow -on-the-H ill and Greenhill": "Harrow-on-the-Hill and Greenhill",
            "Stanmore South J": "Stanmore South",
            "I Wealdstone South": "Wealdstone South",
            "Colham - Cowley": "Colham-Cowley",
            "South Ruisl ip": "South Ruislip",
            "j St. Charles": "St. Charles",
            "- Ferndale": "Ferndale",
            "tCrofton Park": "Crofton Park",
            "Manor Lee J": "Manor Lee",
            "South end": "Southend",
            "Wimbledon West J": "Wimbledon West",
            "West Ham j": "West Ham",
            "Whitton J": "Whitton",
            "Brick layers": "Bricklayers",
            "Rotherhithe J": "Rotherhithe",
            "Carshalton St. Hel ier South": "Carshalton St. Helier South",
            "Wa II ington Sou th": "Wallington South",
            "L:ann Hall": "Cann Hall",
            "Ley ton": "Leyton",
            "r Balham": "Balham",
            "Earlsf ield": "Earlsfield",
            "~utney": "Putney",
            "Shaftesbu ry": "Shaftesbury",
            ") West Hill": "West Hill",
            "Longbridge )": "Longbridge",
            "Village J": "Village",
            "Coiehill": "Colehill",
            "Sui ivan": "Sulivan",
            'Baker Street "-': "Baker Street",
            "Regent's Park J": "Regent's Park",
            "Westbourne J": "Westbourne",
        },
        "columns": ["Con", "Lab", "Lib", "Other"],
        "merged": {
            code: "Other"
            for code in (
                "Comm",
                "NF",
                "SLAG",
                "R",
                "RA",
                "SWAG",
                "RR",
                "Ind R",
                "Ind Lab",
                "ENP",
                "SCO",
                "SLGLF",
                "TC",
                "Ind Con",
                "NIPRA",
                "P Con",
                "CAG",
                "CP",
                "EW",
                "FS",
                "IDF",
                "Ind Lib",
                "NCA",
                "NP",
                "P & AA",
                "PR",
                "PPP",
                "S & T",
                "Ind",
            )
        },
        "state": {
            "Barking": {"Lab": 45, "Other": 4, "Total": 49},
            "Barnet": {"Con": 42, "Lab": 17, "Other": 1, "Total": 60},
            "Bexley": {"Con": 37, "Lab": 22, "Total": 59},
            "Brent": {"Con": 25, "Lab": 35, "Total": 60},
            "Bromley": {"Con": 44, "Lab": 14, "Lib": 2, "Total": 60},
            "Camden": {"Con": 12, "Lab": 48, "Total": 60},
            "Croydon": {"Con": 40, "Lab": 17, "Other": 3, "Total": 60},
            "Ealing": {"Con": 24, "Lab": 36, "Total": 60},
            "Enfield": {"Con": 30, "Lab": 29, "Lib": 1, "Total": 60},
            "Greenwich": {"Con": 8, "Lab": 52, "Total": 60},
            "Hackney": {"Lab": 60, "Total": 60},
            "Hammersmith": {"Con": 10, "Lab": 48, "Lib": 2, "Total": 60},
            "Haringey": {"Con": 19, "Lab": 40, "Other": 1, "Total": 60},
            "Harrow": {"Con": 39, "Lab": 14, "Other": 3, "Total": 56},
            "Havering": {"Con": 20, "Lab": 26, "Other": 9, "Total": 55},
            "Hillingdon": {"Con": 28, "Lab": 32, "Total": 60},
            "Hounslow": {"Con": 23, "Lab": 37, "Total": 60},
            "Islington": {"Lab": 60, "Total": 60},
            "Kensington and Chelsea": {"Con": 45, "Lab": 16, "Total": 61},
            "Kingston upon Thames": {"Con": 39, "Lab": 15, "Lib": 6, "Total": 60},
            "Lambeth": {"Con": 14, "Lab": 46, "Total": 60},
            "Lewisham": {"Con": 11, "Lab": 49, "Total": 60},
            "Merton": {"Con": 29, "Lab": 22, "Other": 3, "Total": 54},
            "Newham": {"Lab": 51, "Other": 9, "Total": 60},
            "Redbridge": {"Con": 45, "Lab": 15, "Total": 60},
            "Richmond upon Thames": {"Con": 36, "Lab": 8, "Lib": 10, "Total": 54},
            "Southwark": {"Con": 4, "Lab": 56, "Total": 60},
            "Sutton": {"Con": 28, "Lab": 13, "Lib": 6, "Other": 4, "Total": 51},
            "Tower Hamlets": {"Lab": 60, "Total": 60},
            "Waltham Forest": {"Con": 12, "Lab": 36, "Total": 48},
            "Wandsworth": {"Con": 12, "Lab": 48, "Total": 60},
            "Westminster, City of": {"Con": 37, "Lab": 23, "Total": 60},
        },
        "parties": {
            "Lab": ("Labour", 1866, 2244017),
            "Con": ("Conservative", 1714, 2184594),
            "Lib": ("Liberal", 1272, 640831),
            "Comm": ("Communist", 125, 17378),
            "NF": ("National Front", 77, 14549),
            "SLAG": ("Save London Action Group", 72, 6226),
            "R": ("Ratepayers Associations", 44, 45288),
            "RA": ("Residents Associations", 37, 35030),
            "SWAG": ("Save Westminster Action Group", 17, 1715),
            "RR": ("Ratepayers and Residents Associations", 13, 14009),
            "Ind R": ("Independent Ratepayers", 7, 8279),
            "Ind Lab": ("Independent Labour", 4, 1721),
            "ENP": ("English National Party", 3, 147),
            "SCO": ("Socialist Current Organisation", 3, 156),
            "SLGLF": ("South London Gay Liberation Front", 3, 201),
            "TC": ("Thamesfield Communities", 3, 946),
            "Ind Con": ("Independent Conservative", 2, 1263),
            "NIPRA": ("National Independence Party and Residents Association", 2, 1806),
            "P Con": ("Powellite Conservative", 2, 115),
            "CAG": ("Community Action Group in Ealing", 1, 269),
            "CP": ("Community Participation", 1, 304),
            "EW": ("An English Westminster", 1, 55),
            "FS": ("Free Socialist", 1, 122),
            "IDF": ("Independent Democratic Front", 1, 1305),
            "Ind Lib": ("Independent Liberal", 1, 526),
            "NCA": ("Neighbourhood Community Action", 1, 320),
            "NP": ("Non-Political", 1, 790),
            "P & AA": ("People and Agrarian Association", 1, 964),
            "PR": ("People's Representative", 1, 560),
            "PPP": ("Positive Progress Party", 1, 15),
            "S & T": ("Squatters and Tenants", 1, 152),
            "Ind": ("Independent", 25, 5971),
        },
    },
    1982: {
        "day": date(1982, 5, 6),
        "url": READS[1982][0],
        "publisher": "Greater London Council",
        "methodology": "Read from the page images of the Greater London Council's scanned report by a vision model, "
        "page by page, with each figure located on the report's text layer. The report lists each ward's candidates "
        "in vote order with the elected flush left and the others indented. Every party's London-wide candidates add "
        "up to the report's table of candidates and votes by party, and its votes do too except for small "
        "differences in four parties (Conservative 38 fewer, Labour 33 more, the Alliance's Liberal and SDP candidates 12 and 1 fewer) "
        "that no reading of the ward figures closes; each borough's elected councillors by "
        "party match its table of the state of the parties in the new councils, each ward's party shares match its "
        "summary of results by wards, and each ward's electorate comes from that summary, whose ward figures add up "
        "to each borough's printed total. The report's errata are applied. Party labels marked in the report as "
        "Labour and Co-operative or as Alliance candidates are stored under the party the report names. Candidate "
        "names are printed as surname and forenames.",
        "candidates": READS[1982][1],
        "wards": (71, 87),
        "ocr": {},
        "columns": [
            "CON",
            "LAB",
            "LIBSDP",
            "LIBFTA",
            "LABCP",
            "SDPLIB",
            "INDRA",
            "RRCON",
            "REA",
            "RAA",
            "INDLAB",
            "RARE",
            "IND",
            "INDRE",
        ],
        "merged": {},
        "residual": {"CON": -38, "LAB": 33, "SDPLIB": -1, "LIBSDP": -12},
        "summary": {"Park Bethnal": ("4,743", "4,748"), "Hoe Street": ("3,669", "8,669")},
        "inserted": {
            ("Merton", "Coll i eros Wood Dundonald"): (
                "Colliers Wood",
                "Dundonald",
                6422,
                3,
                {"Con": 50.4, "Lab": 23.9, "Lib": 25.7},
            ),
            ("Merton", "Ournsford Figge's Marsh"): (
                "Durnsford",
                "Figge's Marsh",
                7149,
                3,
                {"Con": 47.9, "Lab": 31.0, "Lib": 19.3, "Others": 1.7},
            ),
            ("Newham", "Bemersyde Canning Town and Grange"): (
                "Bemersyde",
                "Canning Town and Grange",
                6110,
                2,
                {"Lab": 68.2, "Lib": 31.8},
            ),
            ("Richmond upon Thames", "Richmond HHI Richmond Town"): (
                "Richmond Hill",
                "Richmond Town",
                4642,
                2,
                {"Con": 35.8, "Lab": 10.0, "Lib": 53.3, "Others": 0.9},
            ),
        },
        "codes": {"CCN": "CON", "KEMML": "WEMML", "PNC": "PWC", "SOPLIB": "SDPLIB", "LAECP": "LABCP"},
        "appended": {
            66: [{"kind": "candidate", "text": "Lewis, Robert E.", "party": "COMM", "votes": "159", "indented": True}]
        },
        "people": {(16, "Muir, Anthea"): "Lahr, Anthea", (18, "Selden, John A."): "Seiden, John A."},
        "votes": {
            (9, "Davis, Frank"): 1183,
            (9, "Desmond, Michael B."): 844,
            (9, "Freeman, Michael L."): 1398,
            (9, "Kerens, Harry"): 806,
            (9, "Miller, Montague"): 758,
            (9, "Perkin, Christopher"): 1218,
            (9, "Raff, David"): 1205,
            (11, "Tandy, Colin L."): 2835,
            (13, "Patel, Manibhai D."): 883,
            (14, "Walls, Stephen R."): 925,
            (15, "Coverson, Ron"): 1188,
            (17, "Jowell, Tessa"): 1095,
            (19, "Kent, Reginald H."): 2285,
            (19, "Pickering, Jonathan"): 588,
            (24, "Lennox, Audrey M."): 788,
            (24, "Nellis, Frank"): 1389,
            (28, "Amos, Myra"): 381,
            (28, "Cady, Lorna"): 85,
            (30, "Gray, Ian"): 817,
            (34, "Appleford, Malcolm J."): 885,
            (34, "Cripps, John E."): 1461,
            (34, "Emsley, Vaughan A."): 848,
            (34, "Powell, Lynda A."): 886,
            (34, "Winter, John S."): 543,
            (37, "Age, Sydney H."): 881,
            (37, "Craxton, Michael C."): 793,
            (37, "Minns, Joseph S."): 1251,
            (37, "Rogers, Graham C."): 889,
            (37, "Salisbury, Keith E."): 910,
            (37, "Thomas, Gordon"): 890,
            (37, "York, Colin"): 1580,
            (38, "Doel, Sally J."): 488,
            (38, "Kurtz, Alan H."): 458,
            (39, "Butler, Kerry"): 1188,
            (39, "Crooke, Roland J."): 588,
            (40, "Kent, Patricia A."): 1627,
            (41, "Mitchell, Andrew J.B."): 389,
            (42, "Chapman, Charles H."): 882,
            (43, "Arbuthnot, James N."): 1087,
            (46, "Shakespeare, Anthony J."): 484,
            (47, "Mungol, Hari C."): 683,
            (49, "Hurst, Janet R."): 882,
            (51, "Baskaran, Benjamin C.K."): 198,
            (51, "Cameron, Donald W."): 882,
            (52, "Linford, Margaret E."): 489,
            (56, "Johnston, Paul J."): 1064,
            (58, "Davis, Alan R."): 786,
            (58, "Holland, Johanna"): 268,
            (61, "Smoker, John A."): 897,
            (62, "Beasley, Paul"): None,
            (62, "Thompson, Patricia M."): None,
            (64, "Collen, Kenneth F."): 480,
            (65, "Chope, Christopher R."): 2928,
            (66, "Constable, Martin B."): 880,
            (67, "Demby, Brian G."): 188,
            (68, "Bramall, Lady Germaine M."): 278,
            (68, "Cobbold, Michael D.N."): 1588,
        },
        "withdrawn": {(62, "Islam, Quadratul")},
        "banners": {(52, "NEWHAM")},
        "party": {(51, "Lean, William"): "LAB"},
        "starts": {(22, "ANGEL ROAD"): "Enfield"},
        "state": {
            "Barking and Dagenham": {"CON": 3, "LAB": 37, "LIBSDP": 3, "INDRA": 3, "IND": 2, "Total": 48},
            "Barnet": {"CON": 48, "LAB": 12, "Total": 60},
            "Bexley": {"CON": 41, "LAB": 14, "LIBSDP": 5, "LIBFTA": 2, "Total": 62},
            "Brent": {"CON": 30, "LAB": 33, "LIBFTA": 3, "Total": 66},
            "Bromley": {"CON": 52, "LAB": 5, "LIBSDP": 1, "SDPLIB": 2, "Total": 60},
            "Camden": {"CON": 26, "LAB": 33, "Total": 59},
            "Croydon": {"CON": 62, "LAB": 3, "LABCP": 2, "RARE": 3, "Total": 70},
            "Ealing": {"CON": 37, "LAB": 28, "LIBSDP": 3, "LABCP": 2, "Total": 70},
            "Enfield": {"CON": 47, "LAB": 19, "Total": 66},
            "Greenwich": {"CON": 16, "LAB": 43, "LIBSDP": 2, "SDPLIB": 1, "Total": 62},
            "Hackney": {"CON": 3, "LAB": 50, "LIBFTA": 7, "Total": 60},
            "Hammersmith and Fulham": {"CON": 23, "LAB": 25, "LIBSDP": 2, "Total": 50},
            "Haringey": {"CON": 22, "LAB": 32, "LABCP": 1, "RRCON": 4, "Total": 59},
            "Harrow": {"CON": 41, "LAB": 6, "LIBSDP": 13, "RAA": 3, "Total": 63},
            "Havering": {"CON": 37, "LAB": 12, "LIBSDP": 5, "INDRA": 4, "REA": 4, "RAA": 1, "Total": 63},
            "Hillingdon": {"CON": 57, "LAB": 10, "LIBSDP": 2, "Total": 69},
            "Hounslow": {"CON": 27, "LAB": 33, "Total": 60},
            "Islington": {"LAB": 41, "LABCP": 10, "SDPLIB": 1, "Total": 52},
            "Kensington and Chelsea": {"CON": 39, "LAB": 15, "Total": 54},
            "Kingston upon Thames": {"CON": 40, "LAB": 3, "LIBSDP": 7, "Total": 50},
            "Lambeth": {"CON": 27, "LAB": 32, "LIBSDP": 1, "SDPLIB": 4, "Total": 64},
            "Lewisham": {"CON": 26, "LAB": 41, "Total": 67},
            "Merton": {"CON": 44, "LAB": 13, "Total": 57},
            "Newham": {"LAB": 54, "LIBFTA": 3, "SDPLIB": 3, "Total": 60},
            "Redbridge": {"CON": 51, "LAB": 12, "Total": 63},
            "Richmond upon Thames": {"CON": 26, "LIBSDP": 24, "SDPLIB": 2, "Total": 52},
            "Southwark": {"CON": 8, "LAB": 47, "LABCP": 6, "INDLAB": 3, "Total": 64},
            "Sutton": {"CON": 46, "LAB": 7, "LIBSDP": 3, "Total": 56},
            "Tower Hamlets": {"LAB": 31, "LIBFTA": 18, "IND": 1, "Total": 50},
            "Waltham Forest": {"CON": 25, "LAB": 26, "LIBSDP": 6, "Total": 57},
            "Wandsworth": {"CON": 33, "LAB": 25, "LIBSDP": 1, "LABCP": 2, "Total": 61},
            "Westminster": {"CON": 43, "LAB": 15, "LABCP": 1, "INDRE": 1, "Total": 60},
        },
        "parties": {
            "CON": ("Conservative", 1809, 2310936, 980, "Con"),
            "RRCON": ("Rate Reduction Conservative Party", 17, 12105, 4),
            "LAB": ("Labour", 1876, 1597100, 757, "Lab"),
            "LABCP": ("Labour and Co-operative", 38, 44310, 24, "Lab"),
            "SDPLIB": ("Social Democratic Party", 953, 626086, 13, "Lib"),
            "LIBSDP": ("Liberal", 806, 633407, 78, "Lib"),
            "LIBFTA": ("Liberal", 45, 52152, 33, "Lib"),
            "SDP": ("Social Democratic Party", 18, 11032, 0, "Lib"),
            "LIB": ("Liberal", 7, 5782, 0, "Lib"),
            "IND": ("Independent", 52, 16959, 3),
            "REA": ("Residents' Association", 16, 17517, 4),
            "RAA": ("Ratepayers' Association", 21, 16891, 4),
            "INDRA": ("Independent Ratepayer", 13, 16198, 7),
            "INDLAB": ("Independent Labour", 12, 7959, 3),
            "COMM": ("Communist", 71, 7166, 0),
            "RARE": ("Ratepayers and Residents", 3, 4908, 3),
            "S": ("Southall Party", 9, 4662, 0),
            "E": ("Ecology", 49, 4646, 0),
            "NF": ("National Front", 57, 4162, 0),
            "INDRE": ("Independent Resident", 4, 4004, 1),
            "SD": ("Social Democrat", 9, 1342, 0),
            "INDSDP": ("Independent Social Democratic Party", 7, 1284, 0),
            "IIND": ("Island Independent", 2, 1250, 0),
            "SLAG": ("Save London Action Group", 49, 1172, 0),
            "WRP": ("Workers Revolutionary Party", 16, 982, 0),
            "CW": ("Community Worker", 1, 530, 0),
            "F": ("Fellowship Party", 2, 406, 0),
            "MTA": ("Mercers Tenants Association", 1, 296, 0),
            "AAA": ("Anglo-Asian Alliance", 3, 283, 0),
            "WEMML": ("For Wealth Equality Marxist Maoist Leninist", 1, 273, 0),
            "COMMGB": ("Communist Party of Great Britain", 2, 122, 0),
            "DPL": ("Democratic Pro-Life", 1, 110, 0),
            "INP": ("Irish National Party", 2, 92, 0),
            "SLE": ("Save London Ecology", 2, 74, 0),
            "INDCON": ("Independent Conservative", 1, 73, 0),
            "WP": ("Workers Party", 1, 58, 0),
            "WFCM": ("For Withdrawal from the Common Market", 2, 47, 0),
            "SDPL": ("Social Democrat Pro-Life", 1, 32, 0),
            "PWC": ("Providers Who Care", 1, 22, 0),
            "INDSL": ("Independent Save London", 1, 11, 0),
        },
    },
    1986: {
        "day": date(1986, 5, 8),
        "url": f"{DOWNLOAD}/860bbb06-09d8-4f3f-bd84-b60e442a61e8/LBCE_1986-5-8.pdf",
        "publisher": "London Residuary Body",
        "methodology": "Read from the text layer of the London Residuary Body's scanned report, with a second "
        "reading of candidate names by optical character recognition at 300 dpi and the page images checked where "
        "the two differ. The report lists each ward's candidates in vote order. Every party's London-wide "
        "candidates, votes, and elected add up to the report's table of candidates and votes by party, each "
        "borough's elected councillors match its seats in the summary of results by wards, and each ward's "
        "electorate comes from that summary, whose ward figures add up to each borough's printed total. Party "
        "labels marked in the report as Labour and Co-operative or as Alliance candidates are stored under the "
        "party the report names. Candidate names are printed as surname and forenames.",
        "candidates": (7, 75),
        "wards": (77, 93),
        "byelections": {"holytrinity", "hestonwest"},
        "ocr": {"LIBIT": "LIBFT", "LIIMFT": "LIBAFT"},
        "figures": {
            "Barnes, Rex V. LAB 94-1": "944",
            "Gillie, Mary SDPLIB 21Z": "212",
            "Richards, Norman E. REA l,t97": "1,297",
        },
        "party": {
            (8, "Pepper, Donald"): "IND",
            (55, "Giddins, Grace"): "IND",
            (68, "Smith, Stephen"): "IND",
        },
        "tallied": {"Gumaste, Kishore K.": "LIBSDP"},
        "names": {
            "East Finchley·": "East Finchley",
            "FincWey": "Finchley",
            "Friem Barnet": "Friern Barnet",
            "Bladden": "Blackfen",
            "O~een's Park": "Queen's Park",
            "l\\.oe Green": "Roe Green",
            "Famborough": "Farnborough",
            "Shortiands": "Shortlands",
            "Castiehaven": "Castlehaven",
            "Donners Wells": "Dormers Wells",
            "Elthome": "Elthorne",
            "MOllIlt Pleasant": "Mount Pleasant",
            "Nor1hcote": "Northcote",
            "Amos": "Arnos",
            "liighfield": "Highfield",
            "La!ymer": "Latymer",
            "StJ?eters": "St Peters",
            "Town'": "Town",
            "White City and Sheph<:rds Bush": "White City and Shepherds Bush",
            "Homsey Central": "Hornsey Central",
            "Homsey Vale": "Hornsey Vale",
            "South Homsey": "South Hornsey",
            "South Tottenharn": "South Tottenham",
            "Harrow On The Hill": "Harrow on the Hill",
            "Roxboume": "Roxbourne",
            "01dchurch": "Oldchurch",
            "lckenham": "Ickenham",
            "TOllington": "Tollington",
            "Golbome": "Golborne",
            "Heme Hill": "Herne Hill",
            "Streatharn Wells": "Streatham Wells",
            "LadyweJl": "Ladywell",
            "Dumsford": "Durnsford",
            "Longthomton": "Longthornton",
            "Wandie Valley": "Wandle Valley",
            "Leytons tone": "Leytonstone",
            "Uoyd Park": "Lloyd Park",
            ("Wandsworth", "St Mary's"): "St Mary's Park",
        },
        "votes": {(30, "Brennels, Ann B.M."): 277},
        "people": {
            "Pym, Leslle J.": "Pym, Leslie J.",
            "Simpson, Emest": "Simpson, Ernest",
            "Azecz, Abdool": "Azeez, Abdool",
            "Conway, Naderan": "Conway, Nadezda",
            "Cillysostomou, Rolandos": "Chrysostomou, Rolandos",
            "Lucas, Vicloria G.": "Lucas, Victoria G.",
            "Butler, Vie": "Butler, Vic",
            "Welfare, Damien FJ.": "Welfare, Damien F.J.",
            "Green, Len A. ATA.": "Green, Len A. Al'A.",
            "Thomas, Cella M.": "Thomas, Celia M.",
            "May, Aian S.": "May, Alan S.",
            "Robertson, Ista B.": "Robertson, Isla B.",
            "~ilkinson, Peter B.": "Wilkinson, Peter B.",
            "SlDne, Oliver L.": "Stone, Oliver L.",
            "Vickers, lan": "Vickers, Ian",
            "Allen, Kathlr-en M.": "Allen, Kathleen M.",
            "Hepworth, Be~aminM.": "Hepworth, Benjamin M.",
            "Brooker, Alan lB.": "Brooker, Alan J.B.",
            "Donovan, Eileen 1": "Donovan, Eileen J.",
            "Sterry, lan J.": "Sterry, Ian J.",
            "Wall, lan L.": "Wall, Ian L.",
            "Noyes, lames F.": "Noyes, James F.",
            "MeAndrew, Kenneth": "McAndrew, Kenneth",
            "For~, Leslie J.": "Ford, Leslie J.",
            "Waterhouse, Benjamin W.e.": "Waterhouse, Benjamin W.C.",
            "Randall, Simon J.e.": "Randall, Simon J.C.",
            "Alnsb!', Raymond L.": "Ainsby, Raymond L.",
            "Warren, Kei'h": "Warren, Keith",
            "[)eves, Michael F.": "Deves, Michael F.",
            "Living~ton, John H.": "Livingston, John H.",
            "Morton,· Humphrey M.L.": "Morton, Humphrey M.L.",
            "Hall, JeJ1!lifer B.": "Hall, Jennifer B.",
            "Wunn, Catherine M.e.": "Wunn, Catherine M.C.",
            "Forbes, lain K.": "Forbes, Iain K.",
            "Blackwell, Valerie 0.": "Blackwell, Valerie O.",
            "PatH, Madhavrao N.": "Patil, Madhavrao N.",
            "Logan, lain W.": "Logan, Iain W.",
            "Lawson, Michael 1\\.": "Lawson, Michael K.",
            "AC:ams, Robert": "Adams, Robert",
            "Scr.ofield, Nigel C.": "Schofield, Nigel C.",
            "Hasl.::r, Wa'ter L.": "Hasler, Walter L.",
            "kincaid, Jean D.": "Kincaid, Jean D.",
            "Smythe, Eric J.e.": "Smythe, Eric J.C.",
            "Barrows, Laurna C.1.": "Barrows, Laurna C.I.",
            "Coleman, lain": "Coleman, Iain",
            "Kablr, Sy~d": "Kabir, Syed",
            "Green, Robert a.M.": "Green, Robert O.M.",
            "Dewar, lain": "Dewar, Iain",
            "Parsonage, Clarissa 0.": "Parsonage, Clarissa O.",
            "Key, Hubert J.e.": "Key, Hubert J.C.",
            "lng, Ernest L.": "Ing, Ernest L.",
            "Briggs, Keith e.": "Briggs, Keith C.",
            "Hugo, Mary e.": "Hugo, Mary C.",
            "Campbell, lain P.": "Campbell, Iain P.",
            "HardWick, John F.": "Hardwick, John F.",
            "lanner, Daniel J.M.": "Janner, Daniel J.M.",
            "Rose, Marie M.l.": "Rose, Marie M.I.",
            "Jenkins, Claudia C.c.": "Jenkins, Claudia C.C.",
            "Burton, lvor F.": "Burton, Ivor F.",
            "Knighting, Frank p.w.": "Knighting, Frank P.W.",
            "BUTton, Michael J.": "Burton, Michael J.",
            "~icol, Margaret": "Nicol, Margaret",
            "Hart, Michael C.c.": "Hart, Michael C.C.",
            "Oppenheimer, Jonathan W.s.": "Oppenheimer, Jonathan W.S.",
            "Turner, Madeleine M.l.": "Turner, Madeleine M.I.",
            "Hannah, lain W.H.": "Hannah, Iain W.H.",
            "Kraemer, lohn W.S.": "Kraemer, John W.S.",
            "FitlSimmons', Peggy M.": "Fitzsimmons, Peggy M.",
            "Stock, Davjj J.": "Stock, David J.",
            "PatiJ, Anand N.": "Patil, Anand N.",
            "Oram, Linda e.": "Oram, Linda C.",
            "Payne, David J.e.": "Payne, David J.C.",
            "Johncock, lain A.": "Johncock, Iain A.",
            "Jenkins, Diana M.1.": "Jenkins, Diana M.I.",
            "Hughes; Peter J.": "Hughes, Peter J.",
            "Mackie, lain J.": "Mackie, Iain J.",
            "Newton, lain": "Newton, Iain",
            "Akhtarzia~ Fazal K.": "Akhtarziai, Fazal K.",
            "Tilbury, Charles M.w.": "Tilbury, Charles M.W.",
            "Campbel~ Madeline B.": "Campbell, Madeline B.",
            "Hal~ Michael": "Hall, Michael",
            "Davis, Mark A.a.": "Davis, Mark A.O.",
            "Walker, lain B.": "Walker, Iain B.",
        },
        "postponed": {("Sutton", "Wallington North"): date(1986, 6, 19)},
        "parties": {
            "LAB": ("Labour", 1887, 2104986, 937, "Lab"),
            "LABCP": ("Labour and Co-operative", 26, 34238, 20, "Lab"),
            "CON": ("Conservative", 1835, 2028535, 684, "Con"),
            "OCONC": ("Conservative", 2, 1181, 1, "Con"),
            "SDPLIB": ("Social Democratic Party", 862, 544035, 53, "Lib"),
            "LIBSDP": ("Liberal", 683, 529113, 109, "Lib"),
            "LIBAFT": ("Liberal", 168, 162987, 45, "Lib"),
            "LIBFT": ("Liberal", 49, 55223, 35, "Lib"),
            "SDPAFT": ("Social Democratic Party", 40, 29201, 1, "Lib"),
            "LIBAHT": ("Liberal", 9, 11024, 3, "Lib"),
            "SDPAST": ("Social Democratic Party", 6, 5397, 0, "Lib"),
            "INDNBA": ("Newham Broad Alliance", 12, 4665, 0, "Lib"),
            "LIBACT": ("Liberal", 2, 3414, 1, "Lib"),
            "LIBAST": ("Liberal", 4, 2180, 2, "Lib"),
            "SDPACT": ("Social Democratic Party", 1, 1462, 0, "Lib"),
            "LIB": ("Liberal", 2, 1005, 0, "Lib"),
            "GRE": ("Green", 166, 28867, 0),
            "REA": ("Residents' Association", 14, 16018, 9),
            "RAA": ("Ratepayers' Association", 6, 9936, 3),
            "INDRA": ("Independent Ratepayer", 10, 9686, 5),
            "RA": ("Ratepayers", 7, 8892, 3),
            "IND": ("Independent", 40, 7726, 1),
            "INDLAB": ("Independent Labour", 11, 4296, 0),
            "NF": ("National Front", 21, 2352, 0),
            "IIND": ("Island Independent", 3, 2090, 0),
            "DC": ("Democrats and Commonwealth", 28, 2074, 0),
            "NHLF": ("New Hackney Liberal Focus", 9, 2058, 0),
            "COMM": ("Communist", 19, 2029, 0),
            "COND": ("Conservative Democratic", 3, 2000, 0),
            "INDRE": ("Independent Resident", 3, 1916, 2),
            "INDCON": ("Independent Conservative", 6, 1779, 0),
            "SPA": ("Southall Parents Association", 3, 1644, 0),
            "TEN": ("Tenants Representative", 2, 874, 0),
            "H": ("Humanist", 18, 777, 0),
            "RCOMM": ("Revolutionary Communist", 11, 751, 0),
            "RARE": ("Ratepayers and Residents", 1, 710, 0),
            "UINDG": ("United Independent Group", 7, 570, 0),
            "VIND": ("Village Independent", 4, 529, 0),
            "ISCG": ("Isleworth South Community Group", 2, 287, 0),
            "COMMPP": ("Communist, People before Profit", 3, 249, 0),
            "COMMGB": ("Communist Party of Great Britain", 5, 238, 0),
            "BN": ("British National", 1, 212, 0),
            "WRP": ("Workers Revolutionary Party", 1, 54, 0),
        },
    },
    1978: {
        "day": date(1978, 5, 4),
        "url": f"{DOWNLOAD}/7ff41bee-72bd-4a3e-8cc4-6dca65383247/LBCE_1978-5-4.pdf",
        "candidates": (16, 82),
        "wards": (83, 100),
        "columns": ["Con", "Lab", "Lib", "Res", "Ind Res", "R", "Ind R", "RR", "GCH", "Ind Con", "Ind"],
        "merged": {"Ind RR": "Ind R"},
        "votes": {
            (39, "Kahn, Peter J."): 770,
            (39, "Andrews, Michael J."): 1773,
            (59, "Phipp, Yvonne P."): 1777,
            (69, "Cooper, Geoffrey M."): 2076,
        },
        "elected": {(39, "Gregory, Richard W."): False},
        "repeated": {(39, "Gregory, Richard W.")},
        "residual": {"Lab": -153, "Con": 121, "Lib": -38},
        "names": {
            "Norburyt": "Norbury",
            '"",don"': "Selsdon",
            "Amos": "Arnos",
            "Spri ngfield": "Springfield",
            "Gooshayst": "Gooshays",
            "Col ham": "Colham",
            "Canon bury East": "Canonbury East",
            "f:iiII rise": "Hillrise",
            "~ Gipsy Hill": "Gipsy Hill",
            "Vassal I": "Vassall",
            "Forest Hill* ,": "Forest Hill",
            "Wandie Valley": "Wandle Valley",
            "Wry the Green": "Wrythe Green",
            "Northcote )": "Northcote",
            "Sui ivan": "Sulivan",
            "l Little Venice": "Little Venice",
            "Oueen's Park j": "Queen's Park",
        },
        "missing": {
            20: [("Penton, Roy", ("Kemp, Brian A.", "Lab", 1721, True))],
            26: [("Kelly, Jacqueline", ("Roy, Nirmal C.", "Lab", 1133, False))],
            46: [("Lewis, Joan P.", ("Hammond, Brenda M.", "Ind Res", 440, False))],
            63: [("Lucas, Edward", ("Jones, Denis H.", "Lab", 719, False))],
            75: [("Walker, Edwin", ("Orwell, Beatrice", "Lab", 1063, True))],
        },
        "postponed": {("Croydon", "Norbury"): date(1978, 6, 15), ("Havering", "Gooshays"): date(1978, 5, 25)},
        "state": {
            "Barking": {"Con": 3, "Lab": 42, "Ind R": 3, "Total": 48},
            "Barnet": {"Con": 49, "Lab": 10, "Ind Res": 1, "Total": 60},
            "Bexley": {"Con": 43, "Lab": 18, "Ind Con": 1, "Total": 62},
            "Brent": {"Con": 27, "Lab": 39, "Total": 66},
            "Bromley": {"Con": 48, "Lab": 12, "Total": 60},
            "Camden": {"Con": 26, "Lab": 33, "Total": 59},
            "Croydon": {"Con": 56, "Lab": 11, "RR": 3, "Total": 70},
            "Ealing": {"Con": 41, "Lab": 28, "Ind": 1, "Total": 70},
            "Enfield": {"Con": 41, "Lab": 19, "Total": 60},
            "Greenwich": {"Con": 17, "Lab": 45, "Total": 62},
            "Hackney": {"Con": 1, "Lab": 59, "Total": 60},
            "Hammersmith": {"Con": 24, "Lab": 24, "Lib": 2, "Total": 50},
            "Haringey": {"Con": 17, "Lab": 42, "Total": 59},
            "Harrow": {"Con": 46, "Lab": 12, "Lib": 1, "R": 4, "Total": 63},
            "Havering": {"Con": 38, "Lab": 12, "Res": 9, "Ind R": 4, "Total": 63},
            "Hillingdon": {"Con": 55, "Lab": 14, "Total": 69},
            "Hounslow": {"Con": 24, "Lab": 36, "Total": 60},
            "Islington": {"Con": 2, "Lab": 50, "Total": 52},
            "Kensington and Chelsea": {"Con": 39, "Lab": 13, "GCH": 2, "Total": 54},
            "Kingston upon Thames": {"Con": 44, "Lab": 6, "Total": 50},
            "Lambeth": {"Con": 22, "Lab": 42, "Total": 64},
            "Lewisham": {"Con": 23, "Lab": 44, "Total": 67},
            "Merton": {"Con": 39, "Lab": 15, "Res": 3, "Total": 57},
            "Newham": {"Lab": 57, "R": 3, "Total": 60},
            "Redbridge": {"Con": 50, "Lab": 13, "Total": 63},
            "Richmond upon Thames": {"Con": 34, "Lib": 18, "Total": 52},
            "Southwark": {"Con": 8, "Lab": 56, "Total": 64},
            "Sutton": {"Con": 47, "Lab": 7, "Lib": 2, "Total": 56},
            "Tower Hamlets": {"Lab": 43, "Lib": 7, "Total": 50},
            "Waltham Forest": {"Con": 21, "Lab": 36, "Total": 57},
            "Wandsworth": {"Con": 36, "Lab": 25, "Total": 61},
            "Westminster, City of": {"Con": 39, "Lab": 19, "Ind Res": 2, "Total": 60},
        },
        "parties": {
            "Lab": ("Labour", 1908, 2111872),
            "Con": ("Conservative", 1829, 2642649),
            "Lib": ("Liberal", 992, 343168),
            "NF": ("National Front", 602, 90740),
            "SLA": ("Save London Alliance", 98, 8075),
            "Comm": ("Communist", 97, 9019),
            "R": ("Ratepayers Associations", 66, 39826),
            "WRP": ("Workers Revolutionary Party", 16, 1309),
            "Ind Res": ("Independent Residents Associations", 15, 10566),
            "Res": ("Residents Associations", 13, 19835),
            "SU": ("Socialist Unity", 12, 1731),
            "WIB": ("West Indian Block", 9, 470),
            "WMB": ("We Mean Business", 9, 2013),
            "Ind R": ("Independent Ratepayers Associations", 8, 7515),
            "NETCAT": ("North East Tottenham Campaign Against Traffic", 7, 2625),
            "ELPA": ("East London People's Alliance", 5, 1151),
            "Ind Con": ("Independent Conservative", 5, 5023),
            "RR": ("Ratepayers and Residents Associations", 5, 8289),
            "DNP": ("Democratic National Party", 4, 332),
            "Ind RR": ("Independent Ratepayers and Residents", 4, 6675),
            "Ind RT": ("Independent Ratepayers and Tenants Associations", 4, 842),
            "BUP": ("British United Party", 3, 267),
            "CP": ("Community Participation", 3, 815),
            "DH": ("Decent Housing For All", 2, 197),
            "GCH": ("Golborne Community Hundred", 2, 1439),
            "Ind Lab": ("Independent Labour", 2, 938),
            "LRRAG": ("Limehouse and Ratcliffe Road Action Group", 2, 295),
            "NAACP": ("National Association for the Advancement of Coloured People", 2, 90),
            "AHP": ("Against Higher Prices", 1, 181),
            "ASCDF": ("Anti-Senior Citizens Discrimination Force", 1, 44),
            "CS": ("Christian Socialist", 1, 77),
            "D": ("Democratic", 1, 386),
            "D Lab": ("Democratic Labour", 1, 214),
            "E": ("Electrician", 1, 135),
            "Ecol": ("Ecology", 1, 43),
            "F": ("Fellowship", 1, 329),
            "Ind Lib": ("Independent Liberal", 1, 98),
            "L": ("Labourer", 1, 257),
            "NPP": ("Non-Party Political", 1, 250),
            "Res T": ("Residents and Tenants", 1, 373),
            "RGP": ("Retired Garage Proprietor", 1, 58),
            "SC": ("Senior Citizens", 1, 216),
            "SCP": ("St. Christopher's Party (Anti Abortion)", 1, 30),
            "SW": ("Socialist Worker", 1, 325),
            "TA": ("Theatrical Agent", 1, 169),
            "WTU": ("Women's Truth and Unity", 1, 171),
            "YCL": ("Young Communist League", 1, 78),
            "Ind": ("Independent", 20, 8688),
        },
    },
}
BOROUGHS = {
    **{name: code for name, code in lb.BOROUGHS.items()},
    "Barking": "E09000002",
    "Hammersmith": "E09000013",
    "Westminster, City of": "E09000033",
}
CODES = {
    "Com": "Comm",
    "ResT": "Res T",
    "N.F": "NF",
    "VCL": "YCL",
    "Can": "Con",
    "Con.": "Con",
    "lab": "Lab",
    "Lab.": "Lab",
    "lib": "Lib",
    "YCl": "YCL",
    "Ind r": "Ind R",
    "S&T": "S & T",
    "CGn": "Con",
    "COil": "Con",
    "(on": "Con",
    "sca": "SCO",
    "SLAG/GC": "SLAG",
    "R*": "R",
    "RA*": "RA",
    "RA'\"": "RA",
    "Lib.-": "Lib",
    "Ub": "Lib",
    "Lal>": "Lab",
    "LlA": "LIA",
    "LocalInd": "Local Ind",
    "Corum": "Comm",
    "Con-": "Con",
    "NF-ACM": "NF",
    "Con AM": "Con",
    "Lib AHR": "Lib",
    "C01J1m": "Comm",
    "lab+Co-op": "Lab",
    "lab + Co-op": "Lab",
    "Lab+Co-op": "Lab",
    "Lab +Co-op": "Lab",
    "OM": "UM",
}
TOKENS = {"Can": "Con", "Can.": "Con", "lnd": "Ind", "lnd.": "Ind", "Ind.": "Ind", "IndR": "Ind R"}
UPPER = {
    "INO": "IND",
    "ORE": "GRE",
    "~ON": "CON",
    "CO~": "CON",
    "CO": "CON",
    "NEON": "CON",
    "CONN": "CON",
    "1ND": "IND",
    "LIND": "IIND",
    "LIB~DP": "LIBSDP",
    "UINOO": "UINDG",
}
ALIASES = {"cityofwestminster": "westminster", "exley": "bexley"}
Word = tuple[float, float, float, float, str]


def read_key(sha256: str, page: int) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}/{sha256}/{page}"


def vision(sha256: str, page: int) -> list[dict]:
    message = json.loads(raw.cached(lb.SOURCE, read_key(sha256, page)).path.read_text())
    text = next(block["text"] for block in message["content"] if block["type"] == "text")
    if message["stop_reason"] == "max_tokens":
        text = text[: text.rindex("},{") + 1] + "]}"
    return json.loads(text)["rows"]


def viewed(year: int, sizes: list[tuple[float, float]], book: list[list[Word]]) -> list[dict]:
    spec = BOOKS[year]
    url, (first, last) = READS[year]
    artifact = raw.cached(lb.SOURCE, url)
    out: list[dict] = []
    borough: str | None = None
    ward: dict | None = None
    for number in range(first, last + 1):
        width, height = sizes[number - 1]
        printed = [line for column in columns(book[number - 1], width, True) for line in lb.lines(column)]
        at = 0
        for row in [*vision(artifact.sha256, number), *spec.get("appended", {}).get(number, [])]:
            text = re.sub(r"\s+", " ", row["text"]).strip()
            named = (
                borough_of(text) if row["kind"] == "borough" or (number, text) in spec.get("banners", set()) else None
            )
            if named:
                borough = named
                continue
            if row["kind"] != "candidate":
                head = re.sub(r"(?i)\s*[-–—]?\s*continued\s*$", "", text).strip(" ,.*#+")
                if head != text and ward is not None and fold(ward["printed"]) == fold(head):
                    continue
                borough = spec.get("starts", {}).get((number, head), borough)
                if borough is None:
                    raise ValueError(f"{year} page {number}: ward before a borough: {text}")
                ward = {"borough": borough, "printed": head, "page": number, "lines": []}
                out.append(ward)
                continue
            if ward is None:
                raise ValueError(f"{year} page {number}: candidate before a ward: {text}")
            name = spec.get("people", {}).get((number, text), text)
            if (number, name) in spec.get("withdrawn", set()):
                continue
            code = re.sub(r"\s+", "", row["party"] or "").upper()
            code = spec.get("party", {}).get((number, name), spec.get("codes", {}).get(code, code))
            if code not in spec["parties"]:
                print(f"UNKNOWN {year} p{number}: {name} {row['party']}")
            figure = row["votes"] or ""
            votes = int(re.sub(r"\D", "", figure)) if re.search(r"\d", figure) else None
            votes = spec.get("votes", {}).get((number, name), votes)

            def score(index: int) -> float:
                words = [w[4] for w in printed[index]]
                ratio = difflib.SequenceMatcher(None, fold(name), fold(" ".join(words[:-2]))).ratio()
                return ratio + any(lb.number(w.translate(lb.DIGITS)) == votes for w in words[-2:])

            window = range(at, min(at + 8, len(printed)))
            best = max(window, key=score, default=None)
            if best is None or score(best) < 0.6:
                above = printed[at - 1] if at else None
                below = printed[at] if at < len(printed) else None
                across = below or above or [(0.0, 0.0, width, height, "")]
                top = max(w[3] for w in above) if above else min(w[1] for w in across) - 12
                bottom = min(w[1] for w in below) if below else max(w[3] for w in across) + 12
                if bottom - top < 6:
                    top, bottom = min(top, bottom) - 6, max(top, bottom) + 6
                box = [min(w[0] for w in across), top, max(w[2] for w in across), bottom]
            else:
                at = best + 1
                row_ = printed[best]
                box = [
                    min(w[0] for w in row_),
                    min(w[1] for w in row_),
                    max(w[2] for w in row_),
                    max(w[3] for w in row_),
                ]
            ward["lines"].append(
                {
                    "name": name,
                    "party": code,
                    "votes": votes,
                    "elected": spec.get("elected", {}).get((number, name), not row["indented"]),
                    "page": number,
                    "bbox": box,
                    "width": width,
                    "height": height,
                }
            )
    return [ward for ward in out if ward["lines"]]


def fetch() -> None:
    for spec in BOOKS.values():
        raw.fetch(lb.SOURCE, spec["url"], suffix=".pdf")
    price_in, price_out = batch.PRICES[MODEL]
    for year, (url, (first, last)) in READS.items():
        artifact = raw.fetch(lb.SOURCE, url, suffix=".pdf")
        wanted: dict[str, batch.Want] = {}
        for page in range(first, last + 1):
            png = subprocess.run(
                ["pdftoppm", "-r", "200", "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
                + [str(artifact.path)],
                check=True,
                capture_output=True,
            ).stdout
            image = Image.open(io.BytesIO(png))
            image.thumbnail((LONGEST, LONGEST))
            buffer = io.BytesIO()
            image.save(buffer, "PNG")
            params = MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=8000,
                system=READ_SYSTEM,
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
                            TextBlockParam(type="text", text="Read the headings and candidates on this page."),
                        ],
                    }
                ],
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": READ_SCHEMA}},
            )
            wanted[read_key(artifact.sha256, page)] = (
                params,
                (image.width * image.height / 750 * price_in + 4000 * price_out) / 1e6,
            )
        print(batch.ensure(lb.SOURCE, f"lbce{year}", wanted)[1])


def fold(text: str) -> str:
    return lb.fold(text)


def borough_of(text: str) -> str | None:
    text = re.sub(r"[\s.·-]*(continued|contined|contd)\s*$", "", text.strip(), flags=re.IGNORECASE)
    text = re.sub(r"\s*\(Royal\s*Borough\)", "", text, flags=re.IGNORECASE)
    spaced = re.sub(r"^[|Il1~\[\]\s]*[|Il1~\[\]]\s+|\s+[|Il1~\[\]][|Il1~\[\]\s]*$", "", text)
    boxed = re.sub(r"^[|Il1~\[\]\s]+|[|Il1~\[\]\s]+$", "", text)
    for key in (fold(text), fold(spaced), fold(boxed)):
        key = ALIASES.get(key, key)
        for name in BOROUGHS:
            if fold(name) == key:
                return name
    if "," in text or len(text) > 40 or any(re.fullmatch(r"[\d.%()]+", t) for t in text.split()):
        return None
    key = fold(re.sub(r"(?i)\s*-\s*con\w*\s*$", "", boxed)).replace("0", "o")
    scored = max((difflib.SequenceMatcher(None, key, fold(name)).ratio(), name) for name in BOROUGHS)
    return scored[1] if scored[0] >= 0.85 and len(key) >= 5 else None


def columns(words: list[Word], width: float, rightmost: bool = False) -> list[list[Word]]:
    span = range(int(width * 0.38), int(width * 0.62), 2)
    crossing = {x: sum(w[0] <= x <= w[2] for w in words) for x in span}
    if rightmost:
        clear = [x for x in span if crossing[x] <= min(crossing.values()) + 1]
        run = [clear[-1]]
        while run[-1] - 2 in clear:
            run.append(run[-1] - 2)
        split = run[len(run) // 2]
    else:
        split = min(span, key=lambda x: (crossing[x], abs(x - width / 2)))
    return [[w for w in words if w[0] < split], [w for w in words if w[0] >= split]]


def upper_code(text: str) -> str:
    code = re.sub(r"[^A-Za-z0-9~\[]", "", text).upper()
    code = re.sub(r"^(UB|UN|LM|LFFI|LID|LIA|L1B|LLB|LIO)", "LIB", code)
    code = re.sub(r"^(SDP|SOP)(UB|LLB|LID|L1B)$", "SDPLIB", code).replace("SOP", "SDP")
    code = re.sub(r"(?<=LIB)F[FIR]$", "FT", re.sub(r"AF[FIR\[]$", "AFT", code))
    code = re.sub(r"^SDPL(M|JB)$", "SDPLIB", re.sub(r"AS[FI]$", "AST", re.sub(r"^INO", "IND", code)))
    return UPPER.get(code, code)


def cleaned(name: str) -> str:
    name = re.sub(r"^(?:[^A-Za-z]+|[Il1]{1,3}[!|]?\s+|il\s+)+(?=[A-Z])", "", name.strip())
    name = re.sub(r"\.(?=,)|(?<=[A-Z])\.(?=\.)|'$|(?<=[A-Z]) (?=\.[A-Z])", "", name)
    name = re.sub(r",(?=[A-Za-z])", ", ", re.sub(r"(?<=[A-Z]),(?=[A-Z]\.)", ".", name))
    name = re.sub(r"(?<= )1\.$", "J.", name)
    name = re.sub(r"(?<=[a-z])(?<!Mc)(?<!Mac)I(?=[a-z])", "l", name)
    name = re.sub(r"(?<=[a-z])lD(?=[a-z])", "to", name)
    name = re.sub(r"(?<=[a-z])Ij(?=[a-z])", "rj", name)
    name = re.sub(r"(?<=[A-Za-z])1(?=[a-z])|(?<=[a-z])1\b", "l", name)
    name = re.sub(r"\bChrislopher\b", "Christopher", name)
    return re.sub(r"\s+", " ", name)


def candidate(
    line: list[Word], codes: set[str], ocr: dict[str, str], plain: bool = False
) -> tuple[str, str, int | None] | None:
    if len(line) > 4 and [w[4] for w in line[-2:]] == ["r", line[-1][4]] and re.fullmatch(r"\d", line[-3][4]):
        line = [*line[:-3], (*line[-3][:4], f"{line[-3][4]},{line[-1][4]}")]
    found = read(line, codes, ocr, plain)
    while found is None and len(line) > 3 and re.fullmatch(r"[.,;:'\"*r-]+|\.\d", line[-2][4]):
        line = [*line[:-2], line[-1]]
        found = read(line, codes, ocr, plain)
    return found


def read(
    line: list[Word], codes: set[str], ocr: dict[str, str], plain: bool = False
) -> tuple[str, str, int | None] | None:
    texts = [w[4] for w in line]
    if len(texts) > 3 and ocr and texts[-1] in {"-", "—", "•", "·"}:
        texts.pop()
    if len(texts) > 3 and texts[-1] == texts[-2] and re.fullmatch(r"[\d,]+", texts[-1]):
        texts.pop()
    if len(texts) < 3:
        return None
    if re.fullmatch(r"(?i)unopposed|uncontested|un-", texts[-1]):
        votes = None
    else:
        figure = texts[-1].replace("'", "").replace("~", "")
        votes = int(figure) if plain and re.fullmatch(r"\d{1,5}", figure) else lb.number(figure)
        if votes is None and ocr and re.fullmatch(r"\d{4,5}", figure.translate(lb.DIGITS)):
            votes = int(figure.translate(lb.DIGITS))
        if votes is None:
            return None
    rest = [TOKENS.get(t, t) for t in texts[:-1]]
    for size in (3, 2, 1):
        if len(rest) <= size:
            continue
        code = " ".join(rest[-size:])
        name = " ".join(rest[:-size])
        if size == 1 and code not in codes and re.fullmatch(r".*\.[A-Z][a-z]+", code):
            head, _, code = code.rpartition(".")
            name = f"{name} {head}."
        for attempt in (code, re.sub(r"[*·•]+$", "", code), re.sub(r"(t|tt|\*|\*\*|·)$", "", code)):
            attempt = ocr.get(attempt, CODES.get(attempt, attempt))
            if attempt not in codes and ocr:
                attempt = upper_code(attempt)
                attempt = ocr.get(attempt, attempt)
            if attempt in codes:
                return name, attempt, votes
    return None


def table2(year: int, sizes: list[tuple[float, float]], book: list[list[Word]]) -> list[dict]:
    spec = BOOKS[year]
    aliases = spec.get("aliases", {})
    codes = set(spec["parties"]) | set(aliases)
    first, last = spec["candidates"]
    out: list[dict] = []
    borough: str | None = None
    ward: dict | None = None
    for number in range(first, last + 1):
        width = sizes[number - 1][0]
        right: str | None = None
        for index, column in enumerate(columns(book[number - 1], width, "ocr" in spec)):
            rows = lb.lines(column)
            left = min((row[0][0] for row in rows), default=0)
            if index and right:
                borough = right
            for row in rows:
                text = " ".join(w[4] for w in row)
                after = re.search(r"(?i)\bcontinued\s+(\S.*)$", text)
                if after and borough_of(after.group(1)):
                    right = borough_of(after.group(1))
                if text in spec.get("figures", {}):
                    row = [*row[:-1], (*row[-1][:4], spec["figures"][text])]
                    text = " ".join(w[4] for w in row)
                found = candidate(row, codes, spec.get("ocr", {}), spec.get("plain", False))
                if found:
                    if ward is None:
                        raise ValueError(f"{year} page {number}: candidate before a ward: {text}")
                    name, code, votes = found
                    code = aliases.get(code, code)
                    if "ocr" in spec:
                        name = spec.get("people", {}).get(cleaned(name), cleaned(name))
                    code = spec.get("party", {}).get((number, name), code)
                    for before, line in spec.get("missing", {}).get(number, []):
                        if name.startswith(before):
                            lower = min(w[1] for w in row)
                            ward["lines"].append(
                                {
                                    "name": line[0],
                                    "party": line[1],
                                    "votes": line[2],
                                    "elected": line[3],
                                    "page": number,
                                    "bbox": line[4]
                                    if len(line) > 4
                                    else [min(w[0] for w in row), lower - 12, max(w[2] for w in row), lower - 3],
                                    "width": width,
                                    "height": sizes[number - 1][1],
                                }
                            )
                    votes = spec.get("votes", {}).get((number, name), votes)
                    if (number, name) in spec.get("repeated", set()) and any(
                        line["name"] == name and line["votes"] == votes for line in ward["lines"]
                    ):
                        continue
                    box = [
                        min(w[0] for w in row),
                        min(w[1] for w in row),
                        max(w[2] for w in row),
                        max(w[3] for w in row),
                    ]
                    ward["lines"].append(
                        {
                            "name": name,
                            "party": code,
                            "votes": votes,
                            "elected": spec.get("elected", {}).get((number, name), row[0][0] - left < 6),
                            "page": number,
                            "bbox": box,
                            "width": width,
                            "height": sizes[number - 1][1],
                        }
                    )
                    continue
                if len(row) >= 3 and lb.number(row[-1][4]) is not None:
                    print(f"UNPARSED {year} p{number}: {text}")
                bare = re.sub(r"[-·•\s]+c\s*[oa]\s*[nm]\s*\w*\s*$", "", text, flags=re.IGNORECASE)
                named = None if spec.get("titled") and bare.upper() == bare else borough_of(text)
                if named:
                    borough = named
                    left = row[0][0]
                    continue
                if "aliases" in spec and "=" in text:
                    continue
                head = re.sub(r"\s*-\s*c\s*o\s*[nm]\s*\w*\s*$", "", text, flags=re.IGNORECASE)
                if spec.get("titled"):
                    head = re.sub(r"\s*[-·•]\s*c\s*[oa]\s*[nm]\s*\w*\s*$", "", head, flags=re.IGNORECASE)
                cont = head != text
                head = head.split(",")[0] if "," in head else re.split(r"\.\s+(?=[A-Z][a-z])", head)[0]
                head = head.strip(" ,.*")
                letters = re.sub(r"[^A-Za-z]", "", head)
                first = re.sub(r"[^A-Za-z]", "", head.split()[0]) if head.split() else ""
                if len(letters) >= 3 and len(first) >= 2 and sum(c.isupper() for c in first) / len(first) > 0.6:
                    if borough is None or re.match(r"TABLE \d", head):
                        continue
                    left = row[0][0]
                    if cont and ward is not None:
                        continue
                    ward = {"borough": borough, "printed": head, "page": number, "column": index, "lines": []}
                    if fold(head) not in spec.get("byelections", set()) or not any(
                        w["borough"] == borough and fold(w["printed"]) == fold(head) for w in out
                    ):
                        out.append(ward)
    return out


STRICT = re.compile(
    r"(?:Mc|Mac|O'|D'|de |Le |La |van )?[A-Z][a-z'\-]+(?:[ -][A-Z][a-z'\-]+)*, "
    r"[A-Z][a-z'\-]+(?: [A-Z][a-z'\-]+)*(?: [A-Z]\.(?:[A-Z]\.)*)?"
)


def reread(year: int, heads: list[dict]) -> None:
    spec = BOOKS[year]
    artifact = raw.cached(lb.SOURCE, spec["url"])
    codes = set(spec["parties"])
    known: dict[str, int] = {}
    for head in heads:
        for line in head["lines"]:
            for word in re.findall(r"[A-Za-z]{3,}", line["name"]):
                known[word] = known.get(word, 0) + 1

    def score(name: str) -> tuple[bool, int]:
        return bool(STRICT.fullmatch(name)), sum(known.get(w, 0) > 1 for w in re.findall(r"[A-Za-z]{3,}", name))

    changed = 0
    for head in heads:
        for line in head["lines"]:
            if line["name"] in spec.get("people", {}).values():
                continue
            _, page = lb.scan(artifact, line["page"], dpi=300)
            scale = 300 / 72
            x0, y0, x1, y1 = (v * scale for v in line["bbox"])
            words = sorted(
                (
                    w
                    for row in page
                    for w in row
                    if x0 - 30 <= w[0] and w[2] <= x1 + 30 and y0 - 8 <= (w[1] + w[3]) / 2 <= y1 + 8
                ),
                key=lambda w: w[0],
            )
            found = candidate(words, codes, spec["ocr"]) if words else None
            if found is None or found[2] != line["votes"]:
                continue
            name = cleaned(found[0])
            if name.count(" ") != line["name"].count(" ") or score(name) < score(line["name"]):
                continue
            line["name"] = spec.get("people", {}).get(name, name)
            changed += 1
    print(f"{year}: {changed} names taken from the second reading")


def joined(row: list[Word], fixes: dict[str, tuple[str, str]]) -> list[Word]:
    text = " ".join(w[4] for w in row)
    for start, (bad, good) in fixes.items():
        if text.startswith(start):
            texts = [w[4] for w in row]
            for i in range(len(row)):
                for j in range(i + 1, len(row) + 1):
                    if " ".join(texts[i:j]) == bad:
                        row = [*row[:i], (row[i][0], row[i][1], row[j - 1][2], row[i][3], good), *row[j:]]
                        break
                else:
                    continue
                break
    out: list[Word] = []
    for w in row:
        if out and re.fullmatch(r"\d{1,3}", out[-1][4]) and re.fullmatch(r"[,.]\d{3}", w[4]) and w[0] - out[-1][2] < 4:
            out[-1] = (out[-1][0], min(out[-1][1], w[1]), w[2], max(out[-1][3], w[3]), out[-1][4] + w[4])
        else:
            out.append(w)
    return out


BANNER = r"[A-Z][A-Z5}{)(lI,]*|and|upon|of|\(Royal|Borough\)|c\S*nued"


def table3(year: int, book: list[list[Word]]) -> dict[str, list[tuple]]:
    first, last = BOOKS[year]["wards"]
    headings = BOOKS[year].get("headings", {})
    found: dict[str, list[tuple]] = {}
    borough: str | None = None
    for number in range(first, last + 1, BOOKS[year].get("step", 1)):
        page = book[number - 1]
        heads = [w[0] for w in page if re.fullmatch(r"(?i)cons?tituency|parliamentary", w[4])]
        cut = min(heads) - 4 if heads else 140
        marks = sorted(((w[0] + w[2]) / 2, w[4]) for w in page if re.fullmatch(r"\([1-9]\)|\(f\$\)|\(6\)'?", w[4]))
        if headings:
            marks = sorted(((w[0] + w[2]) / 2, w[4]) for w in page if w[4] in ("Con", "Lab", "Lib", "Others"))
        shares_x = [x for x, _ in marks[-4:]]
        pending = banner = ""
        rows_ = [joined(row, BOOKS[year].get("summary", {})) for row in lb.lines(page)]
        for index, row in enumerate(rows_):
            if headings:
                if row and row[0][4] == "Ward":
                    row = row[1:]
                if row and all(re.fullmatch(BANNER, w[4]) for w in row):
                    banner = f"{banner} {' '.join(w[4] for w in row if not re.fullmatch(r'c\S*nued', w[4]))}"
                    named = headings.get(banner.strip()) or borough_of(banner)
                    if named:
                        borough = named
                        found.setdefault(borough, [])
                        pending = banner = ""
                    continue
                banner = ""
                lead = 0
                while lead < len(row) and re.fullmatch(BANNER, row[lead][4]) and len(row[lead][4]) > 1:
                    lead += 1
                head = " ".join(w[4] for w in row[:lead] if not re.fullmatch(r"c\S*nued", w[4]))
                named = headings.get(head) or borough_of(head)
                if lead and named:
                    borough = named
                    found.setdefault(borough, [])
                    pending = ""
                    row = row[lead:]
                    if not row:
                        continue
                glued: list[Word] = []
                for w in row:
                    if (
                        glued
                        and re.fullmatch(r"\d{1,3}", glued[-1][4])
                        and re.fullmatch(r"\d{2,3}", w[4])
                        and w[0] - glued[-1][2] < 10
                    ):
                        last = glued.pop()
                        w = (last[0], min(last[1], w[1]), w[2], max(last[3], w[3]), f"{last[4]},{w[4]}")
                    glued.append(w)
                row = [(*w[:4], re.sub(r"^(\d{1,3})['\-·,:](\d)$", r"\1.\2", w[4])) for w in glued]
            text = " ".join(w[4] for w in row)
            named = borough_of(text)
            if named and (re.match(r"[^a-z]*[A-Z]{2}", text) or not re.search(r"\d", text)):
                borough = named
                found.setdefault(borough, [])
                pending = ""
                continue
            if borough is None:
                continue
            figures = [
                w
                for w in row
                if re.fullmatch(r"\d{1,3}[,.]\d{3}", w[4].translate(lb.DIGITS).strip(",.'"))
                and not (headings and w[4].endswith("'"))
            ]
            name = " ".join(w[4] for w in row if w[0] < cut and not re.fullmatch(r"['\d.,%()]+", w[4])).strip(" *}")
            rows = found[borough]
            if not figures:
                after = rows_[index + 1] if index + 1 < len(rows_) else []
                ahead = " ".join(w[4] for w in after if w[0] < cut and not re.fullmatch(r"['\d.,%()]+", w[4]))
                before = rows_[index - 1] if index else []
                nearer = bool(after and before) and after[0][3] - row[0][3] < row[0][3] - before[0][3] - 1
                indent = bool(after) and after[0][0] - row[0][0] > 6
                joins = (
                    re.search(r"\b(and|&)$", name) or ahead[:1].islower() or not ahead.strip(" *}") or nearer or indent
                )
                if (
                    name
                    and not any("%" in w[4] for w in row)
                    and rows
                    and not pending
                    and not joins
                    and rows[-1][2] == number
                    and fold(rows[-1][0]) != "total"
                ):
                    rows[-1] = (f"{rows[-1][0]} {name}", *rows[-1][1:])
                    continue
                if name:
                    pending = f"{pending} {name}".strip()
                continue
            name, pending = f"{pending} {name}".strip(), ""
            if re.search(r"(?i)\btot[a-z]{2,3}$", name) or any(w[4].lower() == "total" for w in row):
                name = "Total"
            if not name:
                continue
            electorate = figures[0]
            before = [w for w in row if w[2] < electorate[0] and re.fullmatch(r"[1-3]", w[4])]
            seats = int(before[-1][4]) if before and "ocr" in BOOKS[year] else None
            share: dict[str, float] = {}
            for w in row:
                if w[0] > electorate[2] and re.fullmatch(r"\d{1,3}\.\d", w[4]):
                    x = (w[0] + w[2]) / 2
                    if len(shares_x) == 4 and x > shares_x[0] - 25:
                        column = min(range(4), key=lambda i: abs(shares_x[i] - x))
                        share[("Con", "Lab", "Lib", "Others")[column]] = float(w[4])
            rows.append((name, lb.number(electorate[4].strip(",.'")), number, list(electorate[:4]), share, seats))
    return found


def closed(page: list[Word]) -> list[Word]:
    out: list[Word] = []
    for row in lb.lines(page):
        for word in row:
            if out and word[0] - out[-1][2] < 2 and abs(word[3] - out[-1][3]) < 3 and not re.search(r"\d", word[4]):
                last = out.pop()
                word = (last[0], min(last[1], word[1]), word[2], max(last[3], word[3]), last[4] + word[4])
            out.append(word)
    return out


def table1(year: int, sizes: list[tuple[float, float]], book: list[list[Word]]) -> dict[str, list[tuple]]:
    first, last = BOOKS[year]["wards"]
    order = BOOKS[year]["state"]
    found: dict[str, list[tuple]] = {name: [] for name in order}
    count = 0
    for number in range(first, last + 1):
        half = sizes[number - 1][0] / 2
        page = book[number - 1]
        for column in ([w for w in page if w[0] < half], [w for w in page if w[0] >= half]):
            rows = [[w for w in row if re.search(r"[A-Za-z0-9]", w[4])] for row in lb.lines(column)]
            rows = [row for row in rows if row]
            starts = sorted(row[0][0] for row in rows if re.search(r"\d{1,3},\d{3}", " ".join(w[4] for w in row)))
            indent = starts[len(starts) // 2] if starts else 0
            pending = ""
            for words in rows:
                text = " ".join(w[4] for w in words)
                at = next((i for i, w in enumerate(words) if re.fullmatch(r"\d{1,3},\d{3}", w[4])), None)
                if at is None or at == 0 or not re.fullmatch(r"\d{1,2}", words[at - 1][4]):
                    if abs(words[0][0] - indent) < 4 and not re.search(r"\d", text) and not borough_of(text):
                        pending = f"{pending} {text}".strip()
                    else:
                        pending = ""
                    continue
                name = f"{pending} {' '.join(w[4] for w in words[: at - 1])}"
                name = re.sub(r"\((Royal Borough|City of)\)|-continued", "", name).strip()
                pending = ""
                electorate = words[at]
                seats = int(words[at - 1][4])
                figure = int(electorate[4].replace(",", ""))
                found[list(order)[count]].append((name or "Total", figure, number, list(electorate[:4]), {}, seats))
                count += not name
    return found


def match(heads: list[str], listed: list[str]) -> list[int]:
    pairs = sorted(
        (
            (difflib.SequenceMatcher(None, fold(h), fold(t)).ratio(), i, j)
            for i, h in enumerate(heads)
            for j, t in enumerate(listed)
        ),
        reverse=True,
    )
    chosen: dict[int, int] = {}
    used: set[int] = set()
    for ratio, i, j in pairs:
        if i in chosen or j in used:
            continue
        if ratio < 0.5 and len(heads) - len(chosen) > 1:
            continue
        chosen[i] = j
        used.add(j)
    if len(chosen) != len(heads):
        raise ValueError(f"unmatched wards: {[h for i, h in enumerate(heads) if i not in chosen]}")
    return [chosen[i] for i in range(len(heads))]


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    wards, candidates = [], []
    for year, spec in BOOKS.items():
        sizes, book = lb.pages(raw.cached(lb.SOURCE, spec["url"]).path)
        if "split" in spec:
            first, last = spec["split"]
            book = [closed(page) if first <= number <= last else page for number, page in enumerate(book, 1)]
        fixes = spec.get("figures", {})
        book = [
            [(*w[:4], fixes.get((number, w[4]), w[4])) for w in page if fixes.get((number, w[4]), "") is not None]
            for number, page in enumerate(book, 1)
        ]
        heads = viewed(year, sizes, book) if year in READS else table2(year, sizes, book)
        if "ocr" in spec and year not in READS:
            reread(year, heads)
        stats = table1(year, sizes, book) if spec.get("seats") else table3(year, book)
        for (borough, printed_), (renamed, extra, electorate, seats, share) in spec.get("inserted", {}).items():
            rows = stats[borough]
            index = next(i for i, r in enumerate(rows) if r[0] == printed_)
            upper, lower = rows[index][3], rows[index + 1][3]
            box = [upper[0], (upper[1] + lower[1]) / 2, upper[2], (upper[3] + lower[3]) / 2]
            rows[index] = (renamed, *rows[index][1:])
            rows.insert(index + 1, (extra, electorate, rows[index][2], box, share, seats))
        absent = spec.get("absent", set())
        gone: dict[str, int] = {}
        sizes_: dict[str, int | None] = {}
        bad = []
        for borough, rows in stats.items():
            total = [r for r in rows if fold(r[0]) == "total"]
            listed = [r for r in rows if fold(r[0]) != "total"]
            residual = spec.get("electorate_residual", {}).get(borough, 0)
            if len(total) != 1 or sum(r[1] for r in listed) + residual != total[0][1]:
                bad.append(f"{year} {borough}: electorate {sum(r[1] for r in listed)} against total {total[:1]}")
                continue
            if total[0][5] is not None and spec.get("seats") and sum(r[5] for r in listed) != total[0][5]:
                bad.append(f"{year} {borough}: {sum(r[5] for r in listed)} seats against total {total[:1]}")
                continue
            stats[borough] = [r for r in listed if (borough, r[0]) not in absent]
            gone[borough] = sum(r[5] for r in listed if (borough, r[0]) in absent)
            sizes_[borough] = total[0][5]
        if bad:
            raise ValueError("\n".join(bad))
        printed = spec["parties"]
        groups = {code: figures[4] for code, figures in printed.items() if len(figures) > 4} or {
            "Con": "Con",
            "Lab": "Lab",
            "Lib": "Lib",
        }
        seen: dict[str, list[int]] = {}
        elected: dict[str, dict[str, int]] = {}
        for borough, code in BOROUGHS.items():
            mine = [h for h in heads if h["borough"] == borough]
            if not mine:
                continue
            listed = stats[borough]
            if len(mine) != len(listed):
                raise ValueError(f"{year} {borough}: {len(mine)} wards in the results, {len(listed)} in the summary")
            order = match([h["printed"] for h in mine], [r[0] for r in listed])
            won = elected.setdefault(borough, {})
            for number, (head, index) in enumerate(zip(mine, order, strict=True)):
                row = listed[index]
                name = spec.get("names", {}).get((borough, row[0]), spec.get("names", {}).get(row[0], row[0]))
                lines_ = head["lines"]
                votes = [line["votes"] for line in lines_]
                if row[5] is not None and len(votes) > row[5] and (None in votes or votes[row[5] - 1] != votes[row[5]]):
                    for rank, line in enumerate(lines_):
                        if line["elected"] != (rank < row[5]):
                            print(f"{year} {borough} {name}: indent of {line['name']} reads elected {line['elected']}")
                        line["elected"] = rank < row[5]
                seats = sum(line["elected"] for line in lines_)
                tacit = all(v is None for v in votes)
                if seats == 0 or len(lines_) < seats or row[5] not in (None, seats):
                    print(f"{year} {borough} {name}: {seats} elected of {len(lines_)}, printed seats {row[5]}")
                if not tacit:
                    votes = [v for v in votes if v is not None]
                    ranked = sorted(votes, reverse=True)
                    if votes != ranked:
                        print(f"{year} {borough} {name}: votes out of order {votes}")
                    if [line["elected"] for line in lines_] != [i < seats for i in range(len(lines_))]:
                        print(f"{year} {borough} {name}: elected not at the top")
                    if len(votes) > seats and votes[seats - 1] == votes[seats]:
                        print(f"{year} {borough} {name}: tie for the last seat {votes}")
                if not tacit and row[4]:
                    total = sum(votes)
                    mine_ = {"Con": 0, "Lab": 0, "Lib": 0, "Others": 0}
                    for line in lines_:
                        mine_[groups.get(line["party"], "Others")] += line["votes"] or 0
                    tokens = list(row[4].values())
                    if year in READS and row[3] is not None:
                        middle = (row[3][1] + row[3][3]) / 2
                        tokens = [
                            float(w[4])
                            for w in book[row[2] - 1]
                            if abs((w[1] + w[3]) / 2 - middle) < 3
                            and w[0] > row[3][2]
                            and re.fullmatch(r"\d{1,3}\.\d", w[4])
                        ]
                    off = {
                        k: (round(100 * v / total, 1), row[4].get(k, 0.0))
                        for k, v in mine_.items()
                        if abs(100 * v / total - row[4].get(k, 0.0)) > 0.11
                        and (year not in READS or (v and all(abs(100 * v / total - t) > 0.11 for t in tokens)))
                    }
                    if off:
                        print(f"{year} {borough} {name} p{head['page']}: shares {off}")
                wards.append(
                    {
                        "year": year,
                        "day": spec.get("postponed", {}).get((borough, name), spec["day"]),
                        "borough": borough,
                        "la_code": code,
                        "number": number + 1,
                        "ward": name,
                        "printed": head["printed"],
                        "seats": seats,
                        "tacit": tacit,
                        "electorate": row[1],
                        "page": head["page"],
                        "electorate_page": row[2],
                        "electorate_box": row[3],
                    }
                )
                for rank, line in enumerate(lines_):
                    tally = seen.setdefault(spec.get("tallied", {}).get(line["name"], line["party"]), [0, 0])
                    tally[0] += 1
                    tally[1] += line["votes"] or 0
                    won[line["party"]] = won.get(line["party"], 0) + line["elected"]
                    candidates.append(
                        {
                            "year": year,
                            "la_code": code,
                            "ward": name,
                            "number": rank + 1,
                            "name": forenames(line["name"])
                            if spec.get("forenames")
                            else trailing(line["name"], "aliases" in spec)
                            if spec.get("trailing")
                            else initials(line["name"])
                            if spec.get("initials")
                            else line["name"],
                            "party": line["party"],
                            "votes": line["votes"],
                            "elected": line["elected"],
                            "page": line["page"],
                            "bbox": line["bbox"],
                        }
                    )
        problems = [
            f"{code}: {seen.get(code)} against {figures[1:]}"
            for code, figures in printed.items()
            if figures[1] is not None
            and tuple(seen.get(code, [0, 0])) != (figures[1], figures[2] + spec.get("residual", {}).get(code, 0))
        ]
        problems += [f"unknown party {code}" for code in seen if code not in printed]
        for borough, won in elected.items():
            if sizes_[borough] not in (None, sum(won.values()) + gone.get(borough, 0)):
                problems.append(f"{borough}: {sum(won.values())} elected, printed {sizes_[borough]} seats")
            if "state" not in spec:
                continue
            table = spec["state"][borough]
            if sum(won.values()) + gone.get(borough, 0) != table["Total"]:
                problems.append(f"{borough}: {sum(won.values())} elected, printed {table['Total']}")
            for party_, column in spec["merged"].items():
                won[column] = won.get(column, 0) + won.pop(party_, 0)
            for column, offset in spec.get("offsets", {}).get(borough, {}).items():
                won[column] = won.get(column, 0) + offset
            for column in spec["columns"]:
                if won.get(column, 0) > table.get(column, 0) or (
                    not gone.get(borough) and won.get(column, 0) != table.get(column, 0)
                ):
                    problems.append(f"{borough} {column}: {won.get(column, 0)} elected, printed {table.get(column, 0)}")
            for party_, count in won.items():
                if count and party_ not in spec["columns"] and party_ not in spec["merged"]:
                    problems.append(f"{borough} {party_}: {count} elected, no column in the printed table")
        totals = {code: sum(won.get(code, 0) for won in elected.values()) for code in printed}
        problems += [
            f"{code}: {totals[code]} elected, printed {figures[3]}"
            for code, figures in printed.items()
            if len(figures) > 3 and totals[code] != figures[3]
        ]
        if problems:
            raise ValueError(f"{year}:\n" + "\n".join(problems))
    pl.DataFrame(wards).write_parquet(PARSED / "wards.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "candidates.parquet")
    print(f"{len(wards)} wards and {len(candidates)} candidates")


PEOPLE = {
    "Bro<E:s H. R.": "Brooks, H. R.",
    "TQd{ey G. Mrs": "Tookey, Mrs G.",
    "Cadogan-<>gg G.R.": "Cadogan-Ogg, G. R.",
    "Bull G. Sir, Bt": "Bull, Sir G., Bt",
    "French PoRe": "French, P. R.",
    "Sands WoRe": "Sands, W. R.",
    "Mend! J. H. E.": "Mendl, J. H. E.",
    "o 'Hanlon A. C.": "O'Hanlon, A. C.",
    "Timaeus.R. T.": "Timaeus, R. T.",
    "Roberts J. C. DIV": "Roberts, J. C.",
    "Strevens A. J.iJ.": "Strevens, A. J. J.",
    "Hunt A.RW.": "Hunt, A. R. W.",
    "Brabazon of Tara H. M.": "Brabazon of Tara, The Lady H. M.",
    "Adams, Phyll is I.": "Adams, Phyllis I.",
    "Perki n, Christopher": "Perkin, Christopher",
    "Felber, Maurice B'w.": "Felber, Maurice B.W.",
    "Patel, Mahendra ku mar P.": "Patel, Mahendrakumar P.",
    "Nortcl iff, Cel ia": "Nortcliff, Celia",
    "Good, Keith S.c.": "Good, Keith S.C.",
    "Nortcl iff, Stephen": "Nortcliff, Stephen",
    "Oa kenfu II, Ch risti ne": "Oakenfull, Christine",
    "Sac key , Kwamina K.": "Sackey, Kwamina K.",
    "Paynton, 01 ive": "Paynton, Olive",
    "Mall ison, Catherine": "Mallison, Catherine",
    "Wal ker, Sarah V.": "Walker, Sarah V.",
    "Austi n, Peter W.": "Austin, Peter W.",
    "Sau nders, Beryl": "Saunders, Beryl",
    'Johnson"Susan E.': "Johnson, Susan E.",
    "Inger, Catheri ne M.": "Inger, Catherine M.",
    "Barrows, Laurna C.1.": "Barrows, Laurna C.I.",
    "Du nbar, Joh n": "Dunbar, John",
    "Browne.(;layton, Robert B.": "Browne-Clayton, Robert B.",
    "Renouf, Anthony J'w.": "Renouf, Anthony J.W.",
    "Sweeney, Joh n": "Sweeney, John",
    "Wh itby, Sarah": "Whitby, Sarah",
    "Butler, Lesl ie": "Butler, Leslie",
    "Taylor, Walter R.w.": "Taylor, Walter R.W.",
    "Col borne Hill. George": "Colborne Hill, George",
    "Trott. Leonard J.• J.P.": "Trott, Leonard J., J.P.",
    "Hoepel man, Jack": "Hoepelman, Jack",
    "Brewi ngton, Susan R.": "Brewington, Susan R.",
    "Ox ley, Mary R.": "Oxley, Mary R.",
    "Howarth.Guy B.": "Howarth, Guy B.",
    "Ing. Ernest L., J.P.": "Ing, Ernest L., J.P.",
    "Faul kner, John": "Faulkner, John",
    "Daniell. Eric A .• J.P.": "Daniell, Eric A., J.P.",
    "Lyde, Nicholas F.w.": "Lyde, Nicholas F.W.",
    "Bartrum-Garrett Robin J.": "Bartrum-Garrett, Robin J.",
    "Tilley, ChristineJ.": "Tilley, Christine J.",
    "Lane, PeterW.": "Lane, Peter W.",
    "Ell iott, Douglas R.": "Elliott, Douglas R.",
    "'Hewish, Roger J.L.": "Hewish, Roger J.L.",
    "Henry, Frederi ck": "Henry, Frederick",
    "Mel ntyre, Jacquel ine A.": "McIntyre, Jacqueline A.",
    "G1asspool, Peter J.": "Glasspool, Peter J.",
    "Garfield, Julia C.1.": "Garfield, Julia C.I.",
    "Fletcher, Frederic k": "Fletcher, Frederick",
    "Eldridge Gerard": "Eldridge, Gerard",
    "Dickerson Alfred T.": "Dickerson, Alfred T.",
    "Fleetwood, Kaye J:": "Fleetwood, Kaye J.",
    "Richardson..Paul K.": "Richardson, Paul K.",
    "Skinner, SlIsan": "Skinner, Susan",
    "lake, Sidney J.": "Lake, Sidney J.",
    "0' Kelly, Fergus": "O'Kelly, Fergus",
    "lewis, Ruth E.": "Lewis, Ruth E.",
    "Bourne, C<'lrole S.": "Bourne, Carole S.",
    "Kal ia, Madan": "Kalia, Madan",
    "Wil kie, Ruth": "Wilkie, Ruth",
    "0' Keeffe, Kevin": "O'Keeffe, Kevin",
    "Gri bble, John J.": "Gribble, John J.",
    "Firmin, Mary T.e.": "Firmin, Mary T.C.",
    "Gil bert, Brian": "Gilbert, Brian",
    "Too till , David A.": "Tootill, David A.",
    "Wil kins, Brian C.": "Wilkins, Brian C.",
    "Wil kins, Maureen B.": "Wilkins, Maureen B.",
    "Andrews, David l.": "Andrews, David L.",
    "Wither by, Alan J.": "Witherby, Alan J.",
    "Mallam, Dennis T.l.": "Mallam, Dennis T.L.",
    "Den by, Maeve": "Denby, Maeve",
    "Carroll, Moi ra": "Carroll, Moira",
    "lng, Frank W.": "Ing, Frank W.",
    "Claxton, Angela F.1.": "Claxton, Angela F.I.",
    "Walker-8mith, John J.": "Walker-Smith, John J.",
    "II iffe, Dr. Stephen": "Iliffe, Dr. Stephen",
    "Chall is, Margaret R.": "Challis, Margaret R.",
    "Peel Jonathan H.M.": "Peel, Jonathan H.M.",
    "ZaHar, Mahmood A": "Zaffar, Mahmood A.",
    "SomerviUe, Joyce M.": "Somerville, Joyce M.",
    "Cohen, J.oseph": "Cohen, Joseph",
    "McCormack, B. ~.": "McCormack, B. E.",
    "Bert.ie, E. B.": "Bertie, E. B.",
    "Sharp, ~rs N.G.": "Sharp, Mrs N.G.",
    "Richa rds, P.": "Richards, P.",
    "lIIe-Smith, R.": "Ille-Smith, R.",
    "WobeY,J·": "Wobey, J.",
    "DaIY,J·": "Daly, J.",
    "KirbY,J·": "Kirby, J.",
    "RileY,J·": "Riley, J.",
    "Cer.vantes-Watson, N. J.": "Cervantes-Watson, N. J.",
    "Sinclair, Mrs Lt. G.": "Sinclair, Mrs L. G.",
    "Ajustron, Mi~s M. P.": "Ajustron, Miss M. P.",
    "Hart!.-D. W.": "Hart, D. W.",
    "Alton,-JI B.": "Alton, J. B.",
    "Salisbu ry, K. E.": "Salisbury, K. E.",
    "D' Aubreys, E. P": "D'Aubreys, E. P.",
    "Bay ton, G. E.": "Bayton, G. E.",
    "f-lawthorne, E.": "Hawthorne, E.",
    "Head, MisS' P. A.": "Head, Miss P. A.",
    "Derick, C. a. L.": "Derick, C. O. L.",
    "Pel ling, Mrs A. L.": "Pelling, Mrs A. L.",
    "McK.<lY, J.": "McKay, J.",
    'Lewis,"M rs V. E.': "Lewis, Mrs V. E.",
    "WaY,·G.W.": "Way, G.W.",
    "G urrin, A. C.": "Gurrin, A. C.",
    "Pepper,Mrsl.J.": "Pepper, Mrs I.J.",
    "lng, E. L.": "Ing, E. L.",
    "Tibbie, F. G.": "Tibble, F. G.",
    "TaylGr, B.": "Taylor, B.",
    "Audlev, Miss M.": "Audley, Miss M.",
    "Hamifin, T. J.": "Hanafin, T. J.",
    "Dearie, P. F.": "Dearle, P. F.",
    "Davi~, J. F.": "David, J. F.",
    "Khu~er, Mrs S.": "Khuner, Mrs S.",
    "Ro~hford, F.": "Rochford, F.",
    "Dames, D.": "Darnes, D.",
    "GleDdinning, J. A. C.": "Glendinning, J. A. C.",
    "Sautter, A.": "Soutter, A.",
    "Buddie, A. E.": "Buddle, A. E.",
    "Dflvies, R. W.": "Davies, R. W.",
    "StpWell, F. O.": "Stowell, F. O.",
    "Kingwelr, S. R. W.": "Kingwell, S. R. W.",
    "M~lls, G. F.": "Mills, G. F.",
    "Bard, H.": "Bord, H.",
    "Bard, B. D.": "Bord, B. D.",
    "Sioam, B. B.": "Sloam, B. B.",
    "Blackbume, W. A.": "Blackburne, W. A.",
    "Donna, Mrs E. M.": "Donno, Mrs E. M.",
    "Donna, W. C.": "Donno, W. C.",
    "Fay, Miss M.": "Foy, Miss M.",
    "Thorn, E.": "Thom, E.",
    "Pakenharri, T. F. D.": "Pakenham, T. F. D.",
    "Hal, Mrs A. A.": "Hol, Mrs A. A.",
    "Tarkington, Mrs E. I.": "Torkington, Mrs E. I.",
    "Tarkington, N.": "Torkington, N.",
    "GGldberg, G. H.": "Goldberg, G. H.",
    "Whitmare, A. H.": "Whitmore, A. H.",
    "Tad, E. D. M.": "Tod, E. D. M.",
    "Navarro, Miss V. A.": "Novarro, Miss V. A.",
    "Scanla'n, A. F. G.": "Scanlan, A. F. G.",
    "FridaY, J. R.": "Friday, J. R.",
    "PendsaY, P. G.": "Pendsay, P. G.",
    "powell, G. D. K.": "Powell, G. D. K.",
    "IIlsley, J. A.": "Illsley, J. A.",
    "Kal'ile, P. B.": "Kane, P. B.",
    "KeIlY, D.": "Kelly, D.",
    "Clifford P, A. H.": "Clifford, P. A. H.",
    "01ins F.": "Olins, F.",
    "P~wsey K. A.": "Pawsey, K. A.",
    "Peskin B..": "Peskin, B.",
    "Hu tchins B. A.": "Hutchins, B. A.",
    "l..ewis D. R.": "Lewis, D. R.",
    "Subharwall. M.": "Subharwal, I. M.",
    "litchfield W. G. R.": "Litchfield, W. G. R.",
    "Cusack'J. J.": "Cusack, J. J.",
    "Hughes-Narb orough N.": "Hughes-Narborough, N.",
    "La wrence J. H.": "Lawrence, J. H.",
    "Bull J.'": "Bull, J.",
    "litchfield C.": "Litchfield, C.",
    "i.e Franc L.": "Le Franc, L.",
    "F1intoff I. R. S.": "Flintoff, I. R. S.",
    "Crofton Sir Malby Bt.": "Crofton, Sir Malby, Bt.",
    "Needham'A. H.": "Needham, A. H.",
    "Hay ward C. C.": "Hayward, C. C.",
    "De ~ ey N. K.": "Dewey, N. K.",
    "Philli.ps T. E.": "Phillips, T. E.",
    "Nadav'A.": "Nadav, A.",
    "MarshalIF.W.": "Marshall, F. W.",
    "C1apton B.": "Clapton, B.",
    ".erooks W. F.": "Brooks, W. F.",
    "Carl ton P. A. Mrs": "Carlton, Mrs P. A.",
    "King G. ,A.": "King, G. A.",
    "Countess of Lucan": "Lucan, Countess of",
    "J. Grieve Smith": "Grieve Smith, J.",
}
TITLES = {"Mrs", "Miss", "Dr", "Sir", "Lady", "The", "Major", "Col.", "St.", "de", "la"}


def initials(text: str) -> str:
    text = PEOPLE.get(text, text)
    if "," not in text and ";" not in text:
        text = re.sub(r"^([^.]+?)\.\s*", r"\1, ", text, count=1)
    surname, _, rest = re.sub(r"\s*[,;]\s*", ", ", text.strip(" .'"), count=1).partition(", ")
    surname = re.sub(r"^l(?=[a-z])", "L", surname)
    rest = re.sub(r"\b(Mrs|Miss)\.?(?=[A-Zl1])", r"\1 ", rest)
    out = []
    for token in rest.replace("Mrs.", "Mrs").split():
        if token in TITLES:
            out.append(token)
            continue
        for char in token:
            char = {"l": "L", "1": "I", "0": "O", "o": "O"}.get(char, char)
            if char.isupper():
                out.append(f"{char}.")
    text = f"{surname}, {' '.join(out)}" if out else surname
    return PEOPLE.get(text, text)


PARTICLES = {"de", "De", "der", "van", "Van", "von", "le", "Le", "la", "La", "du", "Du", "da", "St."}


def forenames(text: str) -> str:
    text = PEOPLE.get(text, text).rstrip("*").lstrip("_ ")
    if "," in text:
        return text
    suffix = " ".join(re.findall(r"\(\w+\.?\)", text))
    tokens = re.sub(r"\.(?=[A-Za-z])", ". ", re.sub(r"\s*\(\w+\.?\)", "", text)).split()
    index = len(tokens) - 1
    while index > 0 and tokens[index - 1] in PARTICLES:
        index -= 1
    rest = [{"Mrs.": "Mrs", "Dr.": "Dr", "Mr.": "Mr"}.get(t, t) for t in tokens[:index]]
    if any(not re.fullmatch(r"[A-Z]\.|[A-Z][a-z]+\.?|J-P\.", t) and t not in PARTICLES for t in rest):
        print(f"NAME {text!r}")
    surname = " ".join(tokens[index:])
    out = f"{surname}, {' '.join(rest)}" if rest else surname
    return f"{out}, {suffix}" if suffix else out


HONORIFICS = {
    "Mrs",
    "Miss",
    "Ms",
    "Dr",
    "Sir",
    "Lady",
    "Countess",
    "Rev",
    "Hon",
    "The",
    "Capt",
    "Cdr",
    "Lt",
    "Col",
    "Bt",
    "Major",
    "Lord",
    "Princess",
    "Viscount",
}
LETTERS = {"1": "I", "l": "I", "8": "B", "c": "C", "s": "S"}


def trailing(text: str, noisy: bool = False) -> str:
    text = PEOPLE.get(text, text)
    if noisy and text not in PEOPLE.values():
        text = re.sub(r"\s*[,~•;]+\s*", ". ", text)
        text = re.sub(r"\s+\.", ".", text).replace("..", ".").replace("(Jnr.)", "(Jnr)")
        text = re.sub(r" (?:MIS|Mis|MjS|Mj S|Mr s)$", " Mrs", text)
        text = re.sub(r" Jnr$", " (Jnr)", text)
        text = re.sub(r"(?<=[ .])([pc0])(?=\.|$| )", lambda m: {"0": "O"}.get(m.group(1), m.group(1).upper()), text)
        text = re.sub(r"(?<= )Mc ([A-Z])", r"Mc\1", text).replace("-8", "-S").lstrip(" .'").rstrip(" ·-")
        text = re.sub(r"(?<=[a-z])(?=[A-Z]{2}\.)", " ", text)
        text = re.sub(r"(?<= )([A-Z])(?=[A-Z]\.)", r"\1. ", text)
        text = re.sub(r"(?<=[A-Z]\.)1(?=\.)", "I", text[:1].upper() + text[1:])
    if re.fullmatch(r"[^,]+, \S.*", text):
        return text
    text = re.sub(r"(?<=[a-z])1(?=[a-z])", "l", text.replace("·", "-"))
    suffix = " ".join(re.findall(r"\(\w+\)", text))
    text = re.sub(r"\s*\(\w+\)", "", text)
    text = re.sub(r"(?<=[a-z])(?<!Mc)(?=[A-Z]\.)", " ", text)
    text = re.sub(r"\.(?=[A-Za-z])", ". ", text)
    tokens = [{"S1.": "St."}.get(t, t) for t in text.split()]

    def kind(token: str) -> str | None:
        if token.rstrip(".") in HONORIFICS:
            return "title"
        if re.fullmatch(r"[A-Z1l8cs]\.?|St\.|de|Mc[A-Z]\.?|O'N|Ll\.|[A-Z]-[A-Z]\.", token):
            return "letter"
        return None

    index = next((i for i, t in enumerate(tokens) if i and kind(t)), len(tokens))
    surname, rest = " ".join(tokens[:index]), tokens[index:]
    if any(kind(t) is None for t in rest):
        print(f"NAME {text!r}")
    titles = [{"Mrs.": "Mrs", "Dr.": "Dr"}.get(t, t) for t in rest if kind(t) == "title"]
    letters = [
        t if t in ("St.", "de") or len(t.rstrip(".")) > 1 else f"{LETTERS.get(t[0], t[0])}."
        for t in rest
        if kind(t) == "letter"
    ]
    out = f"{surname}, {' '.join(titles + letters)}" if rest else surname
    return f"{out}, {suffix}" if suffix else out


UNRELATED = {"Senior Citizens"}
RESOLVED = {**lb.GLC_RESOLVED, "British National Party (1960)": ("wikidata", "Q4970528")}


def person(text: str) -> tuple[str, str]:
    text = PEOPLE.get(text, text)
    text = re.sub(r"\s+", " ", text.replace("~", "").replace("•", ",")).strip(" ").lstrip(".")
    if "," not in text:
        text = re.sub(r"^([^.]+?)\.\s+", r"\1, ", text, count=1)
    text = re.sub(r"\s*,\s*", ", ", re.sub(r"[.,]\s*J\.P$", ", J.P.", text))
    surname, _, rest = text.partition(", ")
    rest = re.sub(r"\b([AE])I (?=[a-z])", r"\1l", rest)
    rest = re.sub(r"(?<=[A-Za-z]) (?!(?:de|van|von|la|le|du|da)\b)(?=[a-z])", "", rest)
    rest = re.sub(r"\bl(?=(?:ain|s|r|m)[a-z]*\b)", "I", rest)
    rest = re.sub(r"\b(?<!de )l(?=[a-z])", "L", rest)
    text = f"{surname}, {rest}" if rest else surname
    return text, f"{rest.split(', ')[0]} {surname}".strip()


def emit() -> Path:
    wards = pl.read_parquet(PARSED / "wards.parquet")
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="London borough council elections "
            + ", ".join(map(str, sorted(BOOKS)[:-1]))
            + f", and {max(BOOKS)} by ward, from the reports of the Greater London Council and its successor, "
            "the London Residuary Body",
            polity="GB",
        )
        outranked = foreign_preferred(conn, cs, SOURCE)

        def name(entity: UUID, text: str) -> None:
            if entity in outranked and entity not in cs.minted:
                return
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        gb = cs.polity("GB")
        systems = {system: cs.key("gb.electoral_system", system) for system in ("fptp", "block")}
        if cs.minted & set(systems.values()):
            raise ValueError("publish gb.parliament first")
        known: dict[str, UUID] = {}
        for text, id in conn.execute(
            "select n.text, p.id from party p join polity y on y.id = p.polity_id"
            " join name n on n.entity_id = p.id where y.iso_code = 'GB'"
            " and not exists (select from entity_merge m where m.entity_id = p.id) order by p.id desc"
        ).fetchall():
            known[leh.fold(text)] = id
        others = {
            contest
            for (contest,) in conn.execute(
                "select rs.contest_id from result_set rs join changeset c on c.id = rs.changeset_id "
                "where c.pipeline <> %s and rs.is_default",
                [SOURCE],
            ).fetchall()
        }
        party_ids: dict[str, UUID | None] = {}

        def party(label: str) -> UUID | None:
            if label in INDEPENDENT:
                return None
            if label not in party_ids:
                found = (
                    cs.key(*RESOLVED[label])
                    if label in RESOLVED
                    else None
                    if label in UNRELATED
                    else known.get(leh.fold(label))
                )
                if found is None:
                    found = cs.key("gb.party", slug(label))
                    if found in cs.minted and found not in party_ids.values():
                        cs.add("party", id=found, kind="party", polity_id=gb)
                        name(found, label)
                party_ids[label] = found
            return party_ids[label]

        for year, spec in BOOKS.items():
            day = spec["day"]
            title = f"London Borough Council Elections {day.day} May {year}"
            publisher = spec.get("publisher", PUBLISHER)
            source = cs.source(publisher, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="compendium",
                    publisher=publisher,
                    title=title,
                    url=lb.DATASET,
                    license=f"Published by the {publisher}; the Greater London Authority lists it on the "
                    "London Datastore with no licence stated",
                    license_url=None,
                    attribution=f"{publisher}, {title}.",
                )
            artifact = raw.cached(lb.SOURCE, spec["url"])
            artifact_id = cs.artifact(artifact, source)
            page_ids: dict[int, UUID] = {}

            def page(number: int) -> UUID:
                if number not in page_ids:
                    image = lb.PAGES / f"{artifact.sha256}-{number}.png"
                    if not image.exists():
                        lb.PAGES.mkdir(parents=True, exist_ok=True)
                        subprocess.run(
                            ["pdftoppm", "-r", "100", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                            + [str(artifact.path), str(image.with_suffix(""))],
                            check=True,
                        )
                    width, height = struct.unpack(">II", image.read_bytes()[16:24])
                    page_ids[number] = cs.page(artifact_id, number)
                    cs.add(
                        "source_page",
                        id=page_ids[number],
                        artifact_id=artifact_id,
                        page_number=number,
                        image_key=f"pages/{lb.SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
                return page_ids[number]

            def box(number: int, bbox: list[float]) -> list[float]:
                width, height = sizes[number - 1]
                return [bbox[0] / width, bbox[1] / height, bbox[2] / width, bbox[3] / height]

            sizes, _ = lb.pages(artifact.path)
            national = cs.key("gb.election", f"{day}:local:gb")
            if national in cs.minted:
                cs.add(
                    "election",
                    id=national,
                    polity_id=gb,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(national, f"{year} local elections in England")
                cs.add("slug", path=f"gb/{day}-local", entity_id=national)
            held = wards.filter(pl.col("year") == year)
            lines = candidates.filter(pl.col("year") == year)
            for code in held["la_code"].unique(maintain_order=True):
                area = cs.area("gb.lad", code)
                polity = cs.key("gb.polity", code)
                body = cs.key("gb.body", code)
                if cs.minted & {area, polity, body}:
                    raise ValueError(f"{code}: publish gb.leap first")
                found = conn.execute(
                    "select s.path, n.text from slug s join name n on n.entity_id = s.entity_id and n.is_preferred"
                    " where s.entity_id = %s order by s.is_current desc limit 1",
                    [polity],
                ).fetchone()
                if found is None:
                    raise ValueError(f"{code}: no slug or name for the council")
                path, council = found
                election = cs.key("gb.election", f"{day}:local:{code}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=national,
                    kind="local",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{year} {council} election")
                cs.add("slug", path=f"{path}/{day}-local", entity_id=election)
                cs.add("note", entity_id=election, kind="methodology", body_md=spec.get("methodology", METHODOLOGY))
                if year in NOTES:
                    cs.add("note", entity_id=election, kind="context", body_md=NOTES[year])
                for ward in held.filter(pl.col("la_code") == code).iter_rows(named=True):
                    display = ward["ward"]
                    poll = ward["day"]
                    home = election
                    if poll != day:
                        home = cs.key("gb.election", f"{poll}:local:{code}:{slug(display)}")
                        cs.add(
                            "election",
                            id=home,
                            polity_id=polity,
                            parent_id=election,
                            kind="local",
                            date_from=poll,
                            date_to=poll,
                            status="held",
                        )
                        name(home, f"{year} {council} election, postponed poll in {display}")
                        cs.add("slug", path=f"{path}/{poll}-local", entity_id=home)
                        cs.add("note", entity_id=home, kind="context", body_md=POSTPONED)
                    ward_key = f"{code}:{slug(display)}:{year}"
                    ward_area = cs.area("gb.ward", ward_key)
                    if ward_area in cs.minted:
                        cs.add("area", id=ward_area, scheme_code="gb.ward")
                        cs.add("area_parent", area_id=ward_area, parent_id=area, valid_from=None, valid_to=None)
                        name(ward_area, display)
                    unit = cs.unit("gb.ward", ward_key)
                    if unit in cs.minted:
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
                        name(unit, display)
                    seats = ward["seats"]
                    tacit = ward["tacit"]
                    contest_key = f"{poll}:local:{ward_key}"
                    contest = cs.key("gb.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=home,
                        kind="body_seats",
                        body_id=body,
                        district_area_id=ward_area,
                        magnitude=seats,
                        round_number=1,
                        electoral_system_id=systems["fptp" if seats == 1 else "block"],
                        default_result_kind="tacit" if tacit else "counted",
                    )
                    name(contest, f"{display} ward, {council}, {year}")
                    cs.add("slug", path=f"{path}/{poll}-local/{slug(display)}", entity_id=contest)
                    result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=home,
                        contest_id=contest,
                        stage="official",
                        kind="tacit" if tacit else "counted",
                        status="active",
                        source_id=source,
                        is_default=contest not in others,
                        completeness=1,
                    )
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=source,
                        locator={"page": ward["page"], "borough": ward["borough"], "ward": ward["printed"]},
                    )

                    def value(option: UUID | None, measure: int, figure: int, number: int, bbox: list[float]) -> None:
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            flags=lb.DIGITIZED,
                            value=figure,
                        )
                        x0, y0, x1, y1 = box(number, bbox)
                        cs.add(
                            "value_locator",
                            result_set_id=result_set,
                            unit_id=unit,
                            option_id=option,
                            channel=0,
                            measure=measure,
                            count_round=0,
                            page_id=page(number),
                            source_id=None,
                            x0=x0,
                            y0=y0,
                            x1=x1,
                            y1=y1,
                        )

                    if not tacit and ward["electorate"] is not None:
                        value(None, lb.REGISTERED, ward["electorate"], ward["electorate_page"], ward["electorate_box"])
                    for line in lines.filter((pl.col("la_code") == code) & (pl.col("ward") == display)).iter_rows(
                        named=True
                    ):
                        option_key = f"{contest_key}:{line['number']}"
                        label = spec["parties"][line["party"]][0]
                        owner = party(label)
                        ballot, full = person(line["name"])
                        option = cs.key(f"{SOURCE}.option", option_key)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=owner,
                            ballot_label=ballot,
                        )
                        someone = cs.key(f"{SOURCE}.person", option_key)
                        cs.add("person", id=someone)
                        name(someone, full)
                        cs.add(
                            "candidacy",
                            id=cs.key(f"{SOURCE}.candidacy", option_key),
                            person_id=someone,
                            option_id=option,
                            role="head",
                            ballot_name=ballot,
                            member_of_party_id=owner,
                            is_independent=owner is None,
                        )
                        if line["votes"] is not None:
                            value(option, lb.VOTES, line["votes"], line["page"], line["bbox"])
                        if line["elected"]:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
