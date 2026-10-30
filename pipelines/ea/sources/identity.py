import csv
import json
import re
import unicodedata
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from itertools import pairwise
from pathlib import Path
from uuid import UUID

from ea import names, raw
from ea.changeset import Changeset
from ea.sources.gb import members as commons
from ea.sources.gb import mnis, wikidata_links
from ea.sources.us.wikidata_links import NICKNAMES
from ea.store import connect

SOURCE = "identity"
VERSION = "3"
NEAR = 12 * 366
LONG_GAP = 24 * 365
LONG_SPAN = 50 * 365
MATCHED = ("tr.", "br.")
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
IDENTITIES = (
    "bioguide",
    "wikidata",
    "mnis",
    "ep.mep",
    "lop.person",
    "fec.candidate",
    "us.klarner.person",
    "us.alged.person",
    "us.elstats_local.person",
)
STRONG = ("bioguide", "wikidata", "mnis", "ep.mep", "lop.person", "fec.candidate")
PARTIES = {
    "Colorado's Commonwealth Party, 1930": (
        "6a73b7d9-da80-5785-ac0b-c40a34ad2fc2",
        "d44b6311-7ffb-584b-9ad4-d6914fc29d74",
    ),
    "Colorado's Communist Party, 1924–1942": (
        "0654089f-dae7-503f-987d-71e42d5f7415",
        "5f9b364f-1fce-59ed-b556-e896f2a551a0",
    ),
    "Colorado's Concerns of People Party, 1984–2004": (
        "01a13d06-0e48-761d-8c22-86f2e7e7ea1f",
        "01a13afb-20b0-740c-bd6d-a52e6a8abb52",
    ),
    "Colorado's Farmer-Labor Party, 1920–1932": (
        "784658bc-a316-56a8-bc3c-aa8b949293c3",
        "4be8b07b-af60-57da-afea-1edfc1a00d4e",
    ),
    "Colorado's Independent American Party, 1970": (
        "dcbf5473-de91-50f9-b686-94aab7471796",
        "01a13afb-20b0-740c-bd6d-a53165e39f64",
    ),
    "Colorado's Liberal Party, 1916–1930": (
        "bbc9b07e-bcf8-58a1-8e03-b397ea5a0446",
        "8a77f70c-5c7e-5b47-8e15-df4e2d65bc65",
    ),
    "Colorado's National Statesman Party, 1978": (
        "fa5309a5-be7e-5600-b29f-d5a835a3a010",
        "1453d00e-b831-572e-9b24-b64c604ccd03",
    ),
    "Colorado's National Union Party, 1936": (
        "9b31ab84-1466-574e-a751-810a6a4e9665",
        "ac2dd28b-71f0-51aa-8b20-481bebf58282",
    ),
    "Colorado's New Hispano Party, 1966": (
        "6d16d91c-4e9e-5bc2-aff8-6d4b8a20a8ef",
        "2eb17c29-f482-5770-89e4-328bf42c0ab3",
    ),
    "Colorado's La Raza Unida Party, 1970–1976": (
        "fe633eda-81eb-5f21-8db1-a2ae79e829b2",
        "9b96b5d1-621b-5b48-88ef-2ad5e7267d11",
        "33db7e99-c2d5-594b-a4c8-c1babcc3f90e",
    ),
    "Colorado's Socialist Party, 1910–1954": (
        "a3b2a511-c401-52f1-ab00-d4836a2ece55",
        "d1bc74f5-b397-5683-983b-aa71e5a968c2",
    ),
    "Colorado's Socialist Labor Party, 1910–1962": (
        "d3330fdc-94ee-5ba2-bc03-97303e52b18a",
        "8f73747d-d2fe-597e-948c-eaf984f42f6a",
    ),
    "Colorado's Statesman Party, 1980": (
        "4ea39368-6f1c-5e73-9457-d1fc6bd78d05",
        "50f3b208-c843-527f-8d94-9b4fea0ba075",
    ),
    "Connecticut's George Wallace Party, 1974–1976": (
        "f995b65e-9109-5892-b841-af4954026d38",
        "97b81568-8286-5d0f-8395-1d1246cc9468",
    ),
    "Connecticut's Socialist Party, 1902–1958": (
        "37a7f419-d544-5639-8f07-5d8ecb470398",
        "bae82bde-46dd-5a90-9809-45871a794fd1",
    ),
    "Delaware's American Party, 1970–1986": (
        "01a13d06-0e49-7148-b8b7-0f226c6672d6",
        "9ec92b59-f1ca-5d2e-b8ac-58ce9265d92d",
    ),
    "New York's Peace and Freedom Party, 1966–2024": (
        "1d1a4253-9460-5954-9c80-73e324ba531b",
        "9e0ae2c2-bb58-5025-b3dc-0443e0cdcdd6",
    ),
    "New York's Socialist Labor Party, 1920–1980": (
        "43392e40-d80a-541a-8d4b-9527dfa612cc",
        "e30d5afc-f2ae-5473-b5b8-622c7f2c09e0",
    ),
    "Rhode Island's Peace and Freedom Party, 1970": (
        "252002e9-9af6-5664-a163-7555d61fb8ff",
        "3ed7f2a6-3330-5bae-8408-8affda0f7fc1",
    ),
    "Washington's Peace and Freedom Party, 1966–1968": (
        "830e6219-06ac-5294-aee1-12dbb33083c2",
        "9add6fbe-426e-58df-a5e0-a02d81ed4be7",
    ),
    "Washington's Workers Party of America, 1922–1928": (
        "34bedb9b-7648-5c45-bcc2-b715e74896da",
        "89699ab9-3b06-59cb-8c76-34c897df4079",
    ),
    "Washington's Human Rights Party, 2014–2018": (
        "01a13c42-ee25-76a4-818e-73233326c6fe",
        "01a13c42-ee25-76a4-818e-7335278d98a4",
    ),
    "Wisconsin's Conservative Party, 1968–2000": (
        "c068e259-69d5-54dc-ac8c-a30cd1d84178",
        "e13710af-eecc-5bd4-8f33-fce2aa3ebdee",
    ),
    "Texas's La Raza Unida Party, 1972–1978": (
        "89071ad5-2531-5a81-8dd9-e2d3b75b2bcc",
        "c46e7602-9cd3-5310-8e69-baf380b96868",
        "c7ca1346-d7c8-5142-a33f-00057ca0d204",
    ),
    "New Mexico's La Raza Unida Party, 1976–1982": (
        "6add521b-d73e-59fb-84ea-549247482abd",
        "fc5eb5d4-40d3-5d48-ac40-f08bbb49c75a",
    ),
    "California's Peace and Freedom Party, 1968–2022": (
        "01a13afb-20b0-740c-bd6d-a525349071c9",
        "01a13d06-0e48-761d-8c22-86ebd8eb40c3",
    ),
    "Puerto Rico's New Progressive Party, 1988–2024": (
        "01a13afb-20b1-714a-a374-9742fb668fc3",
        "01a13d06-0e4a-70ac-9628-9d2e0c5a977e",
    ),
    "Puerto Rico's Popular Democratic Party, 1992–2024": (
        "01a13afb-20b1-714a-a374-97448cea3784",
        "01a13d06-0e4a-70ac-9628-9d2fe1550ed1",
    ),
    "Puerto Rico's Independence Party, 1992–2024": (
        "01a13afb-20b1-714a-a374-97436bc4715d",
        "01a13d06-0e4a-70ac-9628-9d31fb7c8a7c",
    ),
    "Virginia's Socialist Party, 1900–1949": (
        "1cf3d06b-c63f-5e1f-8541-d19e1d1568b2",
        "dd1f834d-5292-580e-bcf4-316629e08948",
    ),
    "Minnesota's National Party, 1918": (
        "9c3de61d-c4df-5d37-a21c-1ba3f722474d",
        "c81fd68a-174f-5617-a955-c317d0fd67c7",
    ),
    "Montana's National Party, 1918": ("18f5ba62-f4ea-590f-8dca-a5d6976fa972", "bf15d6a7-643d-5720-99d8-921981a03f71"),
    "Church of the Militant Elvis Party, 2001–2019": (
        "b517c147-266b-5eba-b997-4b6f6de81f19",
        "412743ed-b36a-5909-a462-0b5cdd163dc3",
    ),
    "English National Party, 1974–1979": (
        "1c06bc7e-5b67-51ec-95f3-f4110b32664e",
        "cf5953ef-3fd9-5158-8e74-b5631824f0a9",
    ),
    "Raving Loony Green Giant Party, 1992": (
        "3dcb5389-21c2-5c47-9eaf-7fb8069fd235",
        "f3e4323f-ba5b-5cb5-9bf9-211017bd8cbb",
    ),
    "Socialist Equality Party, 1997–2024": (
        "0173f891-401a-53f5-83c8-16c9680116cc",
        "b7202e7d-975f-522e-9fb6-0b702e67702f",
    ),
    "United Country Party, 1979": ("61f32858-6c1d-505a-a44b-6a3892cd0691", "693e8635-419a-5630-8278-eeccb8410cd3"),
    "United Democratic Party, October 1974": (
        "660f1bd3-ffae-58c7-90eb-de1f2995d6f1",
        "7f9bc066-497d-50c2-9431-c753fd45a8d1",
    ),
    "Coalition Labour, 1918, in ParlGov and Umit": (
        "916e1c91-21d2-5d81-a2fa-75b0001ffce7",
        "49d5173d-bed6-5032-a935-5fa6da7b6a4f",
    ),
    "Canada's Liberal-Labour candidates, 1926–1968": (
        "01a13fb9-5ed7-71fc-acca-7e45df10bcc6",
        "01a13fb9-5ed7-71fc-acca-7e4ad5d5d1ec",
    ),
    "Republican Party of Canada, 1964–1971": (
        "01a13fb9-5ed8-727d-a580-0593f27ddb58",
        "01a13fb9-5ed8-727d-a580-059acefcd61e",
    ),
    "Gesamtdeutsche Partei, 1961 and 1969 (printed GPD in 1969)": (
        "82a4ab1b-17e3-50a9-8b0c-99cc31525c08",
        "18c52535-0f3f-54ed-93f8-0b79a0a03ad4",
    ),
    "Unione Sudamericana Emigrati Italiani, 2006–2018": (
        "6bf66be8-b947-5ab5-9455-21ef9c57ec23",
        "a973ef8e-c9b3-54df-83cf-80c20cfc21cb",
    ),
}
PEOPLE = {
    "Pat Brown, attorney general and governor of California": (
        ("wikidata", "Q724121"),
        ("us.ca_statewide.person", "CA:edmund-g-pat-brown"),
    ),
    "Jerry Brown, governor of California 1975-1983 and 2011-2019": (
        ("wikidata", "Q152451"),
        ("us.algara.person", "CA:edmund-g-brown-jr"),
        ("fec.candidate", "S2CA00088"),
        ("us.ca_sov_xls.person", "CA:jerry-brown"),
    ),
    "Adlai Stevenson I, vice president and 1908 nominee for governor of Illinois": (
        ("bioguide", "S000889"),
        ("us.algara.person", "IL:adlai-e-stevenson"),
    ),
    "Adlai Stevenson III, senator and 1982 nominee for governor of Illinois": (
        ("bioguide", "S000890"),
        ("us.algara.person", "IL:adlai-e-iii-stevenson"),
    ),
    "Joe Manchin, West Virginia legislator 1982-1996, secretary of state, governor, and senator": (
        ("bioguide", "M001183"),
        ("us.klarner.person", "249698"),
        ("us.algara.person", "WV:joe-iii-manchin"),
        ("us.wv_hist.person", "WV:joe-manchin-iii"),
    ),
    "A. James Manchin, West Virginia treasurer and delegate 1998-2002": (
        ("wikidata", "Q4647998"),
        ("us.klarner.person", "255863"),
    ),
    "Bill Walker, governor of Alaska 2014-2018": (
        ("wikidata", "Q18159074"),
        ("us.cage.person", "AK:william-m-bill-walker"),
    ),
    "Mark Dayton, senator and governor of Minnesota": (
        ("bioguide", "D000596"),
        ("fec.candidate", "S2MN00027"),
        ("us.algara.person", "MN:mark-dayton"),
    ),
    "Kimberly Yee, Arizona legislator 2011-2019 and treasurer": (
        ("us.klarner.person", "295403"),
        ("us.medsl_statewide.person", "AZ:kimberly-yee"),
    ),
    "Sandra Kennedy, Arizona legislator 1987-1999 and corporation commissioner": (
        ("us.klarner.person", "10465"),
        ("us.medsl_statewide.person", "AZ:sandra-kennedy"),
    ),
    "Aaron Ogden, with the record of U.S. House election in New Jersey's at-large congressional district": (
        ("wikidata", "Q302436"),
        ("us.thea.person", "NJ:aaron-ogden"),
    ),
    "Alan Dupree Wheat, with the record of U.S. House election in Missouri's 5th congressional district": (
        ("wikidata", "Q548596"),
        ("fec.candidate", "H2MO05139"),
    ),
    "Ben Erdreich, with the record of U.S. House election in Alabama's 6th congressional district": (
        ("wikidata", "Q816481"),
        ("fec.candidate", "H2AL06019"),
    ),
    "Bernard Cazeneuve, with the record of e, constituency 5, first round": (
        ("wikidata", "Q560890"),
        ("fr.cdsp.person", "50:CAZENEUVE:BERNARD"),
    ),
    "L. William Paxon, with the record of U.S. House election in New York's 31st congressional district": (
        ("wikidata", "Q3639996"),
        ("fec.candidate", "H8NY31012"),
    ),
    "William Blaine Richardson, with the record of New Hampshire Republican presidential primary": (
        ("wikidata", "Q311782"),
        ("fec.candidate", "P80003411"),
    ),
    "Bob Barr, with the record of presidential election in Alabama": (
        ("wikidata", "Q540544"),
        ("fec.candidate", "P80005580"),
    ),
    "Carol Moseley Braun, with the record of District of Columbia Democratic presidential primary": (
        ("wikidata", "Q289396"),
        ("fec.candidate", "P40002552"),
    ),
    "Charles H. Pond, with the record of Connecticut Comptroller election": (
        ("wikidata", "Q885605"),
        ("us.statewide.person", "CT:38163"),
    ),
    "Chester Greenough Atkins, with the record of Massachusetts House of Representatives election, Thirty-Third Middlesex": (
        ("wikidata", "Q1070716"),
        ("us.klarner.person", "83339"),
    ),
    "Chris Chocola, with the record of U.S. House Republican primary in Indiana's 3rd congressional district": (
        ("wikidata", "Q980183"),
        ("fec.candidate", "H2IN03111"),
    ),
    "Chris Christie, with the record of New Hampshire Democratic presidential primary": (
        ("wikidata", "Q63879"),
        ("fec.candidate", "P60008521"),
    ),
    "Chuck Morse, with the record of New Hampshire House of Representatives election, Rockingham 28": (
        ("wikidata", "Q16193758"),
        ("us.klarner.person", "142718"),
    ),
    "Clyde Cecil Holloway, with the record of U.S. House election in Louisiana's 8th congressional district": (
        ("wikidata", "Q735333"),
        ("fec.candidate", "H0LA08017"),
    ),
    "Craig Anthony Washington, with the record of Texas House of Representatives Democratic primary, District 86": (
        ("wikidata", "Q1138504"),
        ("us.klarner.person", "219987"),
    ),
    "Dan Frisa, with the record of U.S. House Republican primary in New York's 4th congressional district": (
        ("wikidata", "Q723209"),
        ("fec.candidate", "H2NY04079"),
    ),
    "David Lloyd George, with the record of general election in Caernarvon Boroughs": (
        ("wikidata", "Q134982"),
        ("gb.umit.person", "wls-caernarvon-boroughs:george-david-lloyd"),
    ),
    "David Ray Nagle, with the record of U.S. House election in Iowa's 3rd congressional district": (
        ("wikidata", "Q1176239"),
        ("fec.candidate", "H2IA03028"),
    ),
    "Deval Patrick, with the record of New Hampshire Republican presidential primary": (
        ("wikidata", "Q11100"),
        ("fec.candidate", "P00014407"),
    ),
    "Donald DiFrancesco, with the record of New Jersey General Assembly election, District 22": (
        ("wikidata", "Q889620"),
        ("us.klarner.person", "155554"),
    ),
    "Earl Ray Tomblin, with the record of West Virginia House of Delegates Democratic primary, District 16": (
        ("wikidata", "Q888517"),
        ("us.klarner.person", "250290"),
    ),
    "Floyd Harold Flake, with the record of U.S. House election in New York's 6th congressional district": (
        ("wikidata", "Q3510629"),
        ("fec.candidate", "H6NY06067"),
    ),
    "Francis Gillette, with the record of Connecticut gubernatorial election": (
        ("wikidata", "Q1441508"),
        ("us.elstats_gov.person", "CT:francis-gillette"),
    ),
    "François Bayrou, with the record of ées-Atlantiques, constituency 2, first round": (
        ("wikidata", "Q12963"),
        ("fr.cdsp.person", "64:BAYROU:FRANCOIS"),
    ),
    "Francis Xavier McCloskey, with the record of U.S. House election in Indiana's 8th congressional district": (
        ("wikidata", "Q1443758"),
        ("fec.candidate", "H2IN08029"),
    ),
    "Frank Mariano Tejeda, with the record of Texas House of Representatives Democratic primary, District 57-2": (
        ("wikidata", "Q737918"),
        ("us.klarner.person", "220190"),
    ),
    "Gary E. Johnson, with the record of New Hampshire Republican presidential primary": (
        ("wikidata", "Q352123"),
        ("fec.candidate", "P20002671"),
    ),
    "George Joseph Hochbrueckner, with the record of U.S. House election in New York's 1st congressional district": (
        ("wikidata", "Q82423"),
        ("fec.candidate", "H4NY01030"),
    ),
    "Giorgia Meloni, with the record of er of Deputies, Lazio 2 - U07 (Latina)": (
        ("wikidata", "Q451791"),
        ("it.camera.persona", "302103"),
    ),
    "Glenn Poshard, with the record of U.S. House election in Illinois's 22nd congressional district": (
        ("wikidata", "Q3108818"),
        ("fec.candidate", "H8IL22094"),
    ),
    "Gordon Brown, with the record of general election in Edinburgh South": (
        ("wikidata", "Q10648"),
        ("mnis", "591"),
    ),
    "Gregory H. Laughlin, with the record of U.S. House election in Texas's 14th congressional district": (
        ("wikidata", "Q1544943"),
        ("fec.candidate", "H6TX14033"),
    ),
    "Harley Orrin Staggers Jr., with the record of U.S. House election in West Virginia's 2nd congressional district": (
        ("wikidata", "Q1585518"),
        ("fec.candidate", "H2WV02019"),
    ),
    "Harris W. Fawell, with the record of U.S. House election in Illinois's 13th congressional district": (
        ("wikidata", "Q5665116"),
        ("fec.candidate", "H4IL13043"),
    ),
    "Helen Delich Bentley, with the record of U.S. House election in Maryland's 2nd congressional district": (
        ("wikidata", "Q868577"),
        ("fec.candidate", "H0MD02057"),
    ),
    "Howard Dean, with the record of District of Columbia Democratic presidential primary": (
        ("wikidata", "Q337470"),
        ("fec.candidate", "P40002214"),
    ),
    "James Roy Rowland Jr., with the record of Georgia House of Representatives Democratic primary, District 119": (
        ("wikidata", "Q1676390"),
        ("us.klarner.person", "39904"),
    ),
    "Jack Dalrymple, with the record of North Dakota House of Representatives election, District 22": (
        ("wikidata", "Q770393"),
        ("us.klarner.person", "138727"),
    ),
    "Jacques Chirac, with the record of French presidential election, first round": (
        ("wikidata", "Q2105"),
        ("fr.cdsp.person", "president:jacques-chirac"),
    ),
    "James Hubert Bilbray, with the record of U.S. House election in Nevada's at-large congressional district": (
        ("wikidata", "Q285366"),
        ("fec.candidate", "H6NV01042"),
    ),
    "Jan Brewer, with the record of Arizona House of Representatives election, District 19": (
        ("wikidata", "Q236941"),
        ("us.klarner.person", "10296"),
    ),
    "Jan Meyers, with the record of U.S. House election in Kansas's 3rd congressional district": (
        ("wikidata", "Q372424"),
        ("fec.candidate", "H4KS03014"),
    ),
    "Jane Dee Hull, with the record of Arizona House of Representatives election, District 19": (
        ("wikidata", "Q264297"),
        ("us.klarner.person", "11508"),
    ),
    "Jean-Marc Ayrault, with the record of -Atlantique, constituency 3, first round": (
        ("wikidata", "Q7711"),
        ("fr.cdsp.person", "44:AYRAULT:JEAN MARC"),
    ),
    "Jeb E. Bradley, with the record of U.S. House Democratic primary in New Hampshire's 1st congressional district": (
        ("wikidata", "Q1686078"),
        ("fec.candidate", "H2NH01130"),
    ),
    "Jeb Bush, with the record of New Hampshire Republican presidential primary": (
        ("wikidata", "Q221997"),
        ("fec.candidate", "P60008059"),
    ),
    "Jill Lynette Long Thompson, with the record of U.S. Senate election in Indiana": (
        ("wikidata", "Q514523"),
        ("fec.candidate", "H6IN05026"),
    ),
    "Jim Chapman, with the record of U.S. House election in Texas's 1st congressional district": (
        ("wikidata", "Q1385199"),
        ("fec.candidate", "H6TX01055"),
    ),
    "James Ross Lightfoot, with the record of U.S. House election in Iowa's 5th congressional district": (
        ("wikidata", "Q1271006"),
        ("fec.candidate", "H4IA05078"),
    ),
    "James Charles Slattery, with the record of U.S. House election in Kansas's 2nd congressional district": (
        ("wikidata", "Q1689219"),
        ("fec.candidate", "H2KS02010"),
    ),
    "John Bidwell, with the record of presidential election in Alabama": (
        ("wikidata", "Q1699334"),
        ("us.wikipedia.person", "john-bidwell"),
    ),
    "John George Schmitz, with the record of presidential election in Alabama": (
        ("wikidata", "Q1700152"),
        ("us.wikipedia.person", "john-schmitz"),
    ),
    "John Major, with the record of ary 1974 general election in St Pancras North": (
        ("wikidata", "Q9559"),
        ("mnis", "119"),
    ),
    "John Parker Hale, with the record of presidential election in California": (
        ("wikidata", "Q716927"),
        ("us.wikipedia.person", "john-hale"),
    ),
    "Jon D. Fox, with the record of U.S. House election in Pennsylvania's 13th congressional district": (
        ("wikidata", "Q6270560"),
        ("fec.candidate", "H2PA13052"),
    ),
    "Joseph Patrick Kennedy II, with the record of U.S. House election in Massachusetts's 8th congressional district": (
        ("wikidata", "Q1386420"),
        ("fec.candidate", "H6MA08141"),
    ),
    "Joseph Paul Kolter, with the record of Pennsylvania House of Representatives election, District 14": (
        ("wikidata", "Q6286018"),
        ("us.klarner.person", "194886"),
    ),
    "Larry LaRocco, with the record of U.S. House election in Idaho's 1st congressional district": (
        ("wikidata", "Q446691"),
        ("fec.candidate", "H2ID01010"),
    ),
    "Larry Rhoden, with the record of South Dakota House of Representatives election, District 29": (
        ("wikidata", "Q16193325"),
        ("us.klarner.person", "211968"),
    ),
    "Laurent Fabius, with the record of -Maritime, constituency 4, first round": (
        ("wikidata", "Q217070"),
        ("fr.cdsp.person", "76:FABIUS:LAURENT"),
    ),
    "Manuel Valls, with the record of ne, constituency 1, first round": (
        ("wikidata", "Q10287"),
        ("fr.cdsp.person", "91:VALLS:MANUEL"),
    ),
    "Mark R. Kennedy, with the record of U.S. House Republican primary in Minnesota's 2nd congressional district": (
        ("wikidata", "Q1900180"),
        ("fec.candidate", "H0MN02035"),
    ),
    "Mark W. Neumann, with the record of U.S. House election in Wisconsin's 1st congressional district": (
        ("wikidata", "Q1900315"),
        ("fec.candidate", "H0WI03118"),
    ),
    "Melissa A. Hart, with the record of U.S. House Republican primary in Pennsylvania's 4th congressional district": (
        ("wikidata", "Q3854236"),
        ("fec.candidate", "H0PA04154"),
    ),
    "Albert Michael Espy, with the record of U.S. House election in Mississippi's 2nd congressional district": (
        ("wikidata", "Q1400332"),
        ("fec.candidate", "H6MS02014"),
    ),
    "Mike Huckabee, with the record of New Hampshire Republican presidential primary": (
        ("wikidata", "Q158813"),
        ("fec.candidate", "P80003478"),
    ),
    "Michael E. Sodrel, with the record of U.S. House Republican primary in Indiana's 9th congressional district": (
        ("wikidata", "Q1933523"),
        ("fec.candidate", "H2IN09084"),
    ),
    "Newton Leroy Gingrich, with the record of New Hampshire Republican presidential primary": (
        ("wikidata", "Q182788"),
        ("fec.candidate", "P60003654"),
    ),
    "Nikki R. Haley, with the record of Iowa Republican caucuses": (
        ("wikidata", "Q11668"),
        ("fec.candidate", "P40010977"),
    ),
    "Peter Gerard Torkildsen, with the record of U.S. House election in Massachusetts's 6th congressional district": (
        ("wikidata", "Q1605716"),
        ("fec.candidate", "H2MA06045"),
    ),
    "Peter William Barca, with the record of Wisconsin State Assembly election, District 64": (
        ("wikidata", "Q2078827"),
        ("us.klarner.person", "247879"),
    ),
    "Richard Henry Lehman, with the record of U.S. House election in California's 18th congressional district": (
        ("wikidata", "Q1424689"),
        ("fec.candidate", "H2CA18015"),
    ),
    "Richard Howard Stallings, with the record of U.S. House election in Idaho's 2nd congressional district": (
        ("wikidata", "Q2149678"),
        ("fec.candidate", "H2ID02018"),
    ),
    "Rick Perry, with the record of New Hampshire Republican presidential primary": (
        ("wikidata", "Q215057"),
        ("fec.candidate", "P20003281"),
    ),
    "Henry Scott Baesler, with the record of U.S. House election in Kentucky's 6th congressional district": (
        ("wikidata", "Q2261433"),
        ("fec.candidate", "H2KY06076"),
    ),
    "Stanley Baldwin, with the record of ry 1910 general election in Bewdley": (
        ("wikidata", "Q166635"),
        ("gb.umit.person", "eng-bewdley:baldwin-stanley"),
    ),
    "Steven Harvey Schiff, with the record of U.S. House election in New Mexico's 1st congressional district": (
        ("wikidata", "Q71766"),
        ("fec.candidate", "H8NM01075"),
    ),
    "Susan Molinari, with the record of U.S. House election in New York's 14th congressional district": (
        ("wikidata", "Q3978229"),
        ("fec.candidate", "H0NY14098"),
    ),
    "Sylvester Turner, with the record of Texas House of Representatives Democratic primary, District 139": (
        ("wikidata", "Q16751186"),
        ("us.klarner.person", "222192"),
    ),
    "Theresa May, with the record of general election in North West Durham": (
        ("wikidata", "Q264766"),
        ("mnis", "8"),
    ),
    "Thomas J. Manton, with the record of U.S. House election in New York's 9th congressional district": (
        ("wikidata", "Q7791100"),
        ("fec.candidate", "H8NY09059"),
    ),
    "Victor O. Frazer, with the record of U.S. House election in United States Virgin Islands's at-large congressional district": (
        ("wikidata", "Q2522517"),
        ("fec.candidate", "H2VI00033"),
    ),
    "Wayne Mixson, with the record of Florida House of Representatives election, District 10, Post 2": (
        ("wikidata", "Q731599"),
        ("us.klarner.person", "30425"),
    ),
    "Scotty Baesler, mayor of Lexington 1982-1993 and Kentucky representative": (
        ("bioguide", "B000028"),
        ("us.alged.person", "15539"),
    ),
    "Robin Wonsley, Minneapolis Ward 2 council member from 2021, elected as Robin Wonsley Worlobah": (
        ("us.mpls_evs.person", "robin-wonsley"),
        ("us.alged.person", "24125"),
    ),
    "Jeff Jackson, North Carolina senator 2014-2022, representative 2023-2024, and attorney general": (
        ("wikidata", "Q17183308"),
        ("us.klarner.person", "331143"),
        ("us.medsl_statewide.person", "NC:jeff-jackson"),
    ),
    "Nikki Haley, South Carolina representative 2005-2011, governor, and UN ambassador": (
        ("wikidata", "Q11668"),
        ("us.klarner.person", "268153"),
    ),
    "Bernie Sanders, Liberty Union candidate for senator in 1974 and governor in 1976, independent in 1986": (
        ("wikidata", "Q359442"),
        ("us.clerk.person", "VT:bernard-sanders"),
        ("us.elstats_gov.person", "VT:bernard-sanders"),
    ),
    "Kamala Harris, San Francisco district attorney elected in 2003 and 2007": (
        ("wikidata", "Q10853588"),
        ("us.alged.person", "61715"),
    ),
    "Friedrich Merz, Wikidata record and race records 2025-2025": (
        ("de.bundeswahlleiterin.person", "2025:Merz:Joachim-Friedrich Martin Josef:1955"),
        ("wikidata", "Q566257"),
    ),
    "Massimo D'Alema, Wikidata record and race records 1994-2001": (
        ("it.interno.person", "D'ALEMA:MASSIMO:1949-04-20"),
        ("wikidata", "Q47602"),
    ),
    "Enrico Letta, Wikidata record and race records 1994-2022": (
        ("it.camera.persona", "300127"),
        ("wikidata", "Q674957"),
    ),
    "Édouard Philippe, Wikidata record and race records 2002-2012": (
        ("fr.cdsp.person", "76:PHILIPPE:EDOUARD"),
        ("wikidata", "Q3579995"),
    ),
    "Michel Rocard, Wikidata record and race records 1969-1993": (
        ("fr.cdsp.person", "78:ROCARD:MICHEL"),
        ("wikidata", "Q12946"),
        ("fr.legislatives_1986.person", "78:michel-rocard"),
        ("fr.cdsp.person", "president:michel-rocard"),
    ),
    "Gabriel Attal, Wikidata record and race records 2017-2024": (
        ("fr.cdsp.person", "92:ATTAL:GABRIEL"),
        ("wikidata", "Q30339350"),
    ),
    "Édouard Balladur, Wikidata record and race records 1988-2002": (
        ("fr.cdsp.person", "75:BALLADUR:EDOUARD"),
        ("wikidata", "Q12959"),
        ("fr.cdsp.person", "president:edouard-balladur"),
    ),
    "Justin Trudeau, Wikidata record and race records 2008-2021": (
        ("ca.elections.person", "2013:24055:justin trudeau"),
        ("wikidata", "Q3099714"),
    ),
    "Mark Carney, Wikidata record and race records 2025-2025": (
        ("ca.elections.person", "2023:35067:mark carney"),
        ("wikidata", "Q192533"),
    ),
    "Élisabeth Borne, Wikidata record and race records 2022-2024": (
        ("fr.cdsp.person", "14:BORNE:ELISABETH"),
        ("wikidata", "Q20020731"),
    ),
    "Rishi Sunak, Wikidata record and race records 2015-2024": (
        ("mnis", "4483"),
        ("wikidata", "Q20055561"),
    ),
    "François Fillon, Wikidata record and race records 1986-2017": (
        ("fr.cdsp.person", "72:FILLON:FRANCOIS"),
        ("wikidata", "Q101410"),
        ("fr.cdsp.person", "75:FILLON:FRANCOIS"),
        ("fr.cdsp.person", "president:francois-fillon"),
        ("fr.legislatives_1986.person", "72:francois-fillon"),
    ),
    "Olaf Scholz, Wikidata record and race records 2025-2025": (
        ("de.bundeswahlleiterin.person", "2025:Scholz:Olaf:1958"),
        ("wikidata", "Q61053"),
    ),
    "Lamberto Dini, Wikidata record and race records 1996-1996": (
        ("it.storico.person", "TOSCANA - Firenze 2:DINI:LAMBERTO"),
        ("wikidata", "Q208100"),
    ),
    "Harold E. Sears, Sullivan County commissioner candidate 1976, printed Harold Harold E. Sears in the primary": (
        ("us.elstats_local.person", "NH:43416"),
        ("us.elstats_local.person", "NH:43398"),
    ),
    "Eleanor M. Quinn, Hillsborough County commissioner candidate 1974, printed Eleanor Eleanor M. M. Quinn in the Republican primary": (
        ("us.elstats_local.person", "NH:43523"),
        ("us.elstats_local.person", "NH:43482"),
    ),
    "James Callaghan, Wikidata record and race records 1966-1983": (
        ("mnis", "3326"),
        ("wikidata", "Q9576"),
    ),
    "Raymond Barre, Wikidata record and race records 1986-1997": (
        ("fr.cdsp.person", "69:BARRE:RAYMOND"),
        ("wikidata", "Q12957"),
        ("fr.legislatives_1986.person", "69:raymond-barre"),
        ("fr.cdsp.person", "president:raymond-barre"),
    ),
    "Giuseppe Conte, Wikidata record and race records 2022-2022": (
        ("it.camera.persona", "307926"),
        ("wikidata", "Q53844829"),
    ),
    "Édith Cresson, Wikidata record and race records 1986-1988": (
        ("fr.cdsp.person", "86:CRESSON:EDITH"),
        ("wikidata", "Q230861"),
        ("fr.legislatives_1986.person", "86:edith-cresson"),
    ),
    "Silvio Berlusconi, Wikidata record and race records 1994-2001": (
        ("it.interno.person", "BERLUSCONI:SILVIO:1936-09-29"),
        ("wikidata", "Q11860"),
    ),
    "Lionel Jospin, Wikidata record and race records 1986-2002": (
        ("fr.cdsp.person", "31:JOSPIN:LIONEL"),
        ("wikidata", "Q187099"),
        ("fr.legislatives_1986.person", "75:lionel-jospin"),
        ("fr.cdsp.person", "president:lionel-jospin"),
    ),
    "Nicola Sturgeon, First Minister of Scotland, and her 1992 and 1997 Westminster candidacies": (
        ("wikidata", "Q467112"),
        ("gb.umit.person", "sct-glasgow-shettleston:sturgeon-nicola"),
        ("gb.umit.person", "sct-glasgow-govan:sturgeon-nicola"),
    ),
    "Edwin Poots, MLA for Lagan Valley 1998-2022 and Belfast South from 2022": (
        ("gb.umit.person", "nir-lagan-valley:poots-edwin"),
        ("gb.ni_assembly.person", "2022-05-05:ni-assembly:belfast-south:12"),
    ),
    "Michelle O'Neill, Dungannon councillor for Torrent from 2005 and MLA for Mid Ulster from 2007": (
        ("gb.ni_assembly.person", "2007-03-07:ni-assembly:mid-ulster:12"),
        ("gb.ni_council.person", "2005-05-05:local:DUN/torrent:8"),
    ),
    "Arlene Foster, Fermanagh councillor for Enniskillen from 2005 and MLA for Fermanagh and South Tyrone from 2003": (
        ("gb.ni_assembly.person", "2003-11-26:ni-assembly:fermanagh-and-south-tyrone:4"),
        ("gb.ni_council.person", "2005-05-05:local:FER/enniskillen:6"),
    ),
    "Nicol Stephen, MP for Kincardine and Deeside 1991-1992 and MSP for Aberdeen South 1999-2011": (
        ("mnis", "4239"),
        ("gb.scottish_parliament.person", "nicol-stephen"),
    ),
    "Count Binface, London mayoral candidate 2021-2024 and parliamentary candidate from 2017": (
        ("gb.london.person", "Count Binface"),
        ("gb.umit.person", "eng-uxbridge-and-south-ruislip:binface-count"),
        ("gb.umit.person", "eng-richmond-and-northallerton:binface-count"),
        ("gb.byelections.person", "2023-07-20:eng-uxbridge-and-south-ruislip:count-binface"),
        ("gb.byelections.person", "2026-06-18:eng-makerfield:count-binface"),
        ("gb.byelections.person", "2026-08-13:eng-clacton:count-binface"),
    ),
    "Siân Berry, London Assembly member from 2016, Camden councillor, and MP for Brighton Pavilion from 2024": (
        ("mnis", "5314"),
        ("gb.dc_local.person", "34835"),
    ),
    "Gale Brewer, New York City Council member 2002-2013 and from 2022, and Manhattan Borough President 2014-2021": (
        ("us.alged.person", "34967"),
        ("us.nyc_general.person", "2017-11-07:new-york-city:borough-president-manhattan:gale-a-brewer"),
        ("us.nyc_general.person", "2023-11-07:new-york-city:council-district-6:gale-a-brewer"),
        ("us.nyc_general.person", "2025-11-04:new-york-city:council-district-6:gale-a-brewer"),
    ),
    "Mark Levine, New York City Council member 2014-2021, Manhattan Borough President 2022-2025, and Comptroller": (
        ("us.alged.person", "35254"),
        ("us.nyc_boe.person", "mark-d-levine"),
    ),
    "Sheila Jackson Lee, Houston City Council member 1990-1994, US Representative 1995-2024, and 2023 mayoral candidate": (
        ("bioguide", "J000032"),
        ("us.alged.person", "50841"),
        ("us.wikipedia_mayors.person", "TX:4835000:sheila-jackson-lee"),
    ),
    "Nithya Raman, Los Angeles City Council member for District 4 from 2020 and 2026 mayoral candidate": (
        ("us.alged.person", "70078"),
        ("us.ceda.person", "202000546:202001635"),
        ("us.ceda.person", "202400549:202401593"),
    ),
    "Pete Buttigieg, Mayor of South Bend 2012-2020 and Secretary of Transportation 2021-2025": (
        ("wikidata", "Q7173106"),
        ("us.alged.person", "13139"),
    ),
    "Adrián García, Houston City Council candidate": (
        ("us.alged.person", "50587"),
        ("us.alged.person", "50586"),
    ),
    "F. Michael Kubosh, Houston City Council candidate": (
        ("us.alged.person", "50675"),
        ("us.houston_citysec.person", "michael:kubosh"),
    ),
    "Ana Cubas, Los Angeles City Council candidate": (
        ("us.ceda.person", "201300082:201300330"),
        ("us.alged.person", "69905"),
    ),
    "Augusto Bisani, Los Angeles City Council candidate": (
        ("us.alged.person", "69902"),
        ("us.ceda.person", "200900143:200900522"),
    ),
    "Bob Blumenfield, Los Angeles City Council candidate": (
        ("us.alged.person", "69918"),
        ("us.ceda.person", "202200883:202202056"),
    ),
    "Carolyn Ramsay, Los Angeles City Council candidate": (
        ("us.alged.person", "69944"),
        ("us.ceda.person", "201500058:201500229"),
    ),
    "Christine Essel, Los Angeles City Council candidate": (
        ("us.ceda.person", "200900143:200900524"),
        ("us.alged.person", "69935"),
    ),
    "Cindy Montanez, Los Angeles City Council candidate": (
        ("us.alged.person", "69941"),
        ("us.ceda.person", "201300139:201300516"),
    ),
    "Curren D. Price, Jr., Los Angeles City Council candidate": (
        ("us.ceda.person", "202200886:202202064"),
        ("us.ceda.person", "201300082:201300329"),
        ("us.alged.person", "69943"),
    ),
    "David Ryu, Los Angeles City Council candidate": (
        ("us.ceda.person", "202000546:202001636"),
        ("us.ceda.person", "201500058:201500230"),
        ("us.alged.person", "69959"),
    ),
    "David 'Ty' Vahedi, Los Angeles City Council candidate": (
        ("us.alged.person", "69963"),
        ("us.ceda.person", "200900095:200900399"),
    ),
    "Gilbert 'Gil' Cedillo, Los Angeles City Council candidate": (
        ("us.alged.person", "69980"),
        ("us.ceda.person", "202200879:202202038"),
        ("us.ceda.person", "201300075:201300296"),
        ("us.ceda.person", "201700051:201700196"),
    ),
    "Grace Yoo, Los Angeles City Council candidate": (
        ("us.ceda.person", "202400545:202401574"),
        ("us.ceda.person", "202000542:202001623"),
        ("us.ceda.person", "202400635:202401835"),
        ("us.alged.person", "69988"),
    ),
    "Jay Beeber, Los Angeles City Council candidate": (
        ("us.ceda.person", "201500058:201500237"),
        ("us.ceda.person", "201900034:201900117"),
    ),
    "Joe Bray-Ali, Los Angeles City Council candidate": (
        ("us.ceda.person", "201700051:201700195"),
        ("us.alged.person", "69996"),
    ),
    "John J. Choi, Los Angeles City Council candidate": (
        ("us.ceda.person", "201300077:201300304"),
        ("us.alged.person", "70001"),
    ),
    "John S. Lee, Los Angeles City Council candidate": (
        ("us.ceda.person", "202400546:202401575"),
        ("us.ceda.person", "201900034:201900123"),
        ("us.alged.person", "70019"),
    ),
    "Jose A. Gardea, Los Angeles City Council candidate": (
        ("us.ceda.person", "201300075:201300297"),
        ("us.alged.person", "70009"),
    ),
    "Karo Torossian, Los Angeles City Council candidate": (
        ("us.ceda.person", "201700057:201700233"),
        ("us.alged.person", "70042"),
    ),
    "Kevin De León, Los Angeles City Council candidate": (
        ("us.ceda.person", "202400547:202401582"),
        ("us.ceda.person", "202000544:202001626"),
        ("us.ceda.person", "202400636:202401837"),
        ("us.alged.person", "70040"),
    ),
    "Krystee Clark, Los Angeles City Council candidate": (
        ("us.ceda.person", "201700057:201700219"),
        ("us.alged.person", "70039"),
    ),
    "Loraine Lundquist, Los Angeles City Council candidate": (
        ("us.alged.person", "70047"),
        ("us.ceda.person", "201900034:201900124"),
    ),
    "Mark Ridley-Thomas, Los Angeles City Council candidate": (
        ("us.ceda.person", "202000542:202001620"),
        ("us.alged.person", "70066"),
    ),
    "Marqueece Harris-Dawson, Los Angeles City Council candidate": (
        ("us.alged.person", "70061"),
        ("us.ceda.person", "202400551:202401599"),
    ),
    "Mike Schaefer, Los Angeles City Council candidate": (
        ("us.ceda.person", "201500058:201500240"),
        ("us.ceda.person", "201700057:201700232"),
    ),
    "Mitch O'Farrell, Los Angeles City Council candidate": (
        ("us.ceda.person", "202200980:202202345"),
        ("us.ceda.person", "201300077:201300303"),
        ("us.ceda.person", "202200881:202202048"),
        ("us.alged.person", "70065"),
    ),
    "Monica Rodriguez, Los Angeles City Council candidate": (
        ("us.alged.person", "70067"),
        ("us.ca_la_sov.person", "2026-06-02:CA:0644000:council:7:monica-rodriguez"),
        ("us.ceda.person", "202200885:202202062"),
        ("us.ceda.person", "201700057:201700230"),
    ),
    "Nadine Momoyo Diaz, Los Angeles City Council candidate": (
        ("us.alged.person", "70073"),
        ("us.ceda.person", "202400547:202401578"),
    ),
    "Navraj Singh, Los Angeles City Council candidate": (
        ("us.alged.person", "70079"),
        ("us.ceda.person", "201900034:201900128"),
    ),
    "Nicole Chase, Los Angeles City Council candidate": (
        ("us.alged.person", "70072"),
        ("us.ceda.person", "201700057:201700218"),
    ),
    "Nury Martinez, Los Angeles City Council candidate": (
        ("us.alged.person", "70076"),
        ("us.ceda.person", "201300139:201300517"),
    ),
    "Paul Koretz, Los Angeles City Council candidate": (
        ("us.ceda.person", "200900095:200900396"),
        ("us.alged.person", "70086"),
    ),
    "Paul Krekorian, Los Angeles City Council candidate": (
        ("us.alged.person", "70087"),
        ("us.ceda.person", "200900143:200900526"),
    ),
    "Rudy Melendez, Los Angeles City Council candidate": (
        ("us.ceda.person", "202400548:202401590"),
        ("us.alged.person", "70101"),
    ),
    "Sam Kbushyan, Los Angeles City Council candidate": (
        ("us.ceda.person", "202400548:202401589"),
        ("us.ceda.person", "201300077:201300305"),
    ),
    "Scott Silverstein, Los Angeles City Council candidate": (
        ("us.alged.person", "70120"),
        ("us.ceda.person", "202200883:202202057"),
    ),
    "Tomas O'Grady, Los Angeles City Council candidate": (
        ("us.alged.person", "70126"),
        ("us.ceda.person", "201500058:201500231"),
    ),
    "Tony Cárdenas, Los Angeles City Council candidate": (
        ("us.alged.person", "70122"),
        ("us.alged.person", "70121"),
    ),
    "Chi Ajani Osse, New York City Council candidate": (
        ("us.alged.person", "34826"),
        ("us.nyc_boe.person", "chi-a-osse"),
        ("us.nyc_general.person", "2023-11-07:new-york-city:council-district-36:chi-a-osse"),
        ("us.nyc_boe.person", "chi-ajani-osse"),
    ),
    "Danniel S. Maio, New York City Council candidate": (
        ("us.nyc_general.person", "2023-11-07:new-york-city:council-district-29:danniel-s-maio"),
        ("us.alged.person", "34874"),
    ),
    "Francesca M. Castellanos, New York City Council candidate": (
        ("us.nyc_boe.person", "francesca-m-castellanos"),
        ("us.nyc_boe.person", "francesca-castellanos"),
        ("us.nyc_general.person", "2017-09-12:new-york-city:council-district-10-democratic:francesca-m-castellanos"),
        ("us.alged.person", "34939"),
    ),
    "Jonathan D. Rinaldi, New York City Council candidate": (
        ("us.nyc_general.person", "2023-11-07:new-york-city:council-district-24:jonathan-d-rinaldi"),
        ("us.nyc_general.person", "2025-11-04:new-york-city:council-district-29:jonathan-rinaldi"),
    ),
    "Justin Brannan, New York City Council candidate": (
        ("us.alged.person", "35037"),
        ("us.nyc_general.person", "2023-11-07:new-york-city:council-district-47:justin-brannan"),
    ),
    "Justin E. Sanchez, New York City Council candidate": (
        ("us.nyc_boe.person", "justin-e-sanchez"),
        ("us.alged.person", "81057"),
    ),
    "Pamela Hamilton-Johnson, New York City Council candidate": (
        ("us.alged.person", "35327"),
        ("us.alged.person", "35324"),
        ("us.nyc_boe.person", "pamela-a-hamilton-johnson"),
        ("us.nyc_general.person", "2017-09-12:new-york-city:council-district-12-democratic:pamela-a-hamilton-johnson"),
    ),
    "Pierina Ana Sanchez, New York City Council candidate": (
        ("us.nyc_boe.person", "pierina-a-sanchez"),
        ("us.nyc_general.person", "2023-11-07:new-york-city:council-district-14:pierina-a-sanchez"),
        ("us.alged.person", "35340"),
        ("us.nyc_boe.person", "pierina-ana-sanchez"),
    ),
    "Shah Shahidul Haque, New York City Council candidate": (
        ("us.nyc_general.person", "2025-11-04:new-york-city:council-district-25:shah-s-haque"),
        ("us.alged.person", "35443"),
    ),
    "Simcha Felder, New York City Council candidate": (
        ("us.alged.person", "35436"),
        ("us.nyc_general.person", "2025-11-04:new-york-city:council-district-44:simcha-felder"),
    ),
    "Stylo Sapaskis, New York City Council candidate": (
        ("us.nyc_general.person", "2025-11-04:new-york-city:council-district-11:stylo-sapaskis"),
        ("us.alged.person", "35470"),
    ),
    "Tanya Carmichael, New York City Council candidate": (
        ("us.nyc_general.person", "2023-11-07:new-york-city:council-district-16:tanya-carmichael"),
        ("us.alged.person", "35486"),
    ),
    "Yu-Ching James Pai, New York City Council candidate": (
        ("us.alged.person", "35551"),
        ("us.nyc_general.person", "2023-11-07:new-york-city:council-district-20:yu-ching-james-pai"),
    ),
    "James \"Jamie\" Davis Jr., Tensas Parish police juror, 2023 Louisiana House 21 candidate, and 2026 Senate nominee": (
        ("us.sld_specials.person", "LA:james-jamie-davis-jr"),
        ("fec.candidate", "S6LA00615"),
        ("us.la_local.person", "22107:jamie-lil-stump-davis"),
    ),
}
HONORIFICS = re.compile(
    r"^(?:(?:the\s+)?(?:rt\.?\s+hon\.?|right\s+honou?rable|honou?rable|very\s+rev(?:erend|d)?\.?|rev(?:erend|d)?\.?)\s+"
    r"|(?:mr|mrs|ms|miss|mx|dr|sir|dame|prof(?:essor)?)\.?\s+)+",
    re.IGNORECASE,
)
PLACEHOLDERS = re.compile(
    r"\b(unknown|scatter\w*|write[- ]?ins?|all other|others?|blank|void|none of|uncommitted|vacan\w*|no candidate)\b"
)


def words(text: str) -> tuple[str, frozenset[str]] | None:
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", text.lower().replace("ı", "i")) if not unicodedata.combining(c)
    )
    folded = re.sub(r"\([^)]*\)|\"[^\"]*\"|“[^”]*”", " ", folded)
    folded = re.sub(r"(?<=[a-z])-(?=[a-z])", "", folded)
    if PLACEHOLDERS.search(folded):
        return None
    parts = [part for part in re.split(r"[^a-z]+", folded) if part]
    if not parts:
        return None
    kept = [parts[0], *(part for part in parts[1:] if len(part) > 1 or part in SUFFIXES)]
    if len(kept) < 2:
        return None
    return " ".join(kept), frozenset(part for part in parts[1:] if len(part) == 1)


def emit() -> Path:
    with connect() as conn:
        conn.execute("set statement_timeout = 0")
        busy: set[str] = {
            pipeline
            for (pipeline,) in conn.execute(
                "select distinct pipeline from changeset where status in ('draft', 'pending_review')"
            ).fetchall()
        }
        rows: list[tuple[UUID, str | None, UUID, UUID | None, UUID | None, date, str]] = conn.execute(
            """select cy.person_id, coalesce(cy.ballot_name, n.text), k.district_area_id, k.office_id, k.body_id,
                      e.date_from, c.pipeline
               from candidacy cy
               join contest_option o on o.id = cy.option_id
               join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id
               join changeset c on c.id = cy.changeset_id
               left join lateral (select text from name where entity_id = cy.person_id and is_preferred limit 1) n
                 on true
               where not exists (select from entity_merge m where m.entity_id = cy.person_id)"""
        ).fetchall()
        held: dict[UUID, dict[str, set[str]]] = defaultdict(dict)
        for person, scheme, value in conn.execute(
            """select entity_id, scheme, value from external_id
               where scheme = any(%s) or scheme = 'us.nnv.person' and value ~ '^[A-Z]{2}[0-9]{4}' and value !~ '^AA0000'""",
            [list(IDENTITIES)],
        ).fetchall():
            held[person].setdefault(scheme, set()).add(value)
        created: dict[UUID, datetime] = dict(
            conn.execute("select id, created_at from entity where type = 'person'").fetchall()
        )
        born: dict[UUID, date] = dict(
            conn.execute("select id, birth_date from person where birth_date is not null").fetchall()
        )
        died: dict[UUID, date] = dict(
            conn.execute("select id, death_date from person where death_date is not null").fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "People recorded once per source or per race joined into one person where the same printed name "
                "(ignoring case, accents, middle initials, and a common nickname such as Bill for William, or a first "
                "name printed as its initial when one person on the race line fits it) ran for the same office or body in the same "
                "district within 12 years, and nothing tells them apart (no race for another seat within 60 days, and no "
                "first name on either record that the other's names cannot be), and party labels of one state or country "
                "that sources spelled apart joined by hand"
            ),
            polity=None,
            kind="match",
        )

        dates: dict[UUID, list[date]] = defaultdict(list)
        seats: dict[UUID, set[tuple[date, object, object, object]]] = defaultdict(set)
        given: dict[UUID, set[str]] = defaultdict(set)
        pipelines: dict[UUID, set[str]] = defaultdict(set)
        lines: dict[tuple[object, ...], dict[UUID, list[date]]] = defaultdict(lambda: defaultdict(list))
        initials: dict[tuple[tuple[object, ...], UUID], set[frozenset[str]]] = defaultdict(set)
        canon = {"rudy": "rudolph", "rudi": "rudolph"} | {
            form: first
            for first, forms in NICKNAMES.items()
            for form in forms.split()
            if form not in {"kathleen", "jon", "bert"}
        }
        for person, text, area, office, body, day, pipeline in rows:
            dates[person].append(day)
            pipelines[person].add(pipeline)
            if text is None or area is None or (office is None and body is None) or pipeline.startswith(MATCHED):
                continue
            named = words(text)
            if named is None:
                continue
            first, rest = named[0].split(" ", 1)
            seats[person].add((day, area, office, body))
            given[person].add(first)
            line = (area, office, body, f"{canon.get(first, first)} {rest}")
            lines[line][person].append(day)
            if named[1]:
                initials[line, person].add(named[1])
        spelled: dict[tuple[object, ...], list[tuple[object, ...]]] = defaultdict(list)
        for line in lines:
            first, rest = line[3].split(" ", 1)
            if len(first) > 1:
                spelled[*line[:3], rest, first[0]].append(line)
        for line in [line for line in lines if len(line[3].split(" ", 1)[0]) == 1]:
            first, rest = line[3].split(" ", 1)
            fits = spelled.get((*line[:3], rest, first), [])
            if len(fits) != 1 or len(lines[fits[0]]) != 1:
                continue
            for person, days in lines.pop(line).items():
                lines[fits[0]][person].extend(days)
                if (line, person) in initials:
                    initials[fits[0], person] |= initials.pop((line, person))

        def suspect(person: UUID) -> bool:
            days = sorted(dates[person])
            gaps = [(later - earlier).days for earlier, later in pairwise(days)]
            return (
                bool(pipelines[person] & busy)
                or (days[-1] - days[0]).days > LONG_SPAN
                or max(gaps, default=0) > LONG_GAP
            )

        parent: dict[UUID, UUID] = {}

        def root(node: UUID) -> UUID:
            parent.setdefault(node, node)
            while parent[node] != node:
                parent[node] = parent[parent[node]]
                node = parent[node]
            return node

        considered: set[UUID] = set()
        blocked: list[tuple[UUID, UUID]] = []
        for line, people in lines.items():
            if len(people) < 2:
                continue
            members = [person for person in people if not suspect(person)]
            for index, person in enumerate(members):
                for other in members[index + 1 :]:
                    signed = initials[line, person] | initials[line, other]
                    births = {born[one] for one in (person, other) if one in born}
                    posthumous = any(
                        one in died and (max(dates[two], default=died[one]) - died[one]).days > 365
                        for one, two in ((person, other), (other, person))
                    )
                    rival = any(
                        abs((one[0] - two[0]).days) <= 60 and one[1:] != two[1:]
                        for one in seats[person]
                        for two in seats[other]
                    )
                    strangers = any(
                        not (
                            one == two
                            or canon.get(one, one) == canon.get(two, two)
                            or (len(one) == 1 or len(two) == 1)
                            and one[0] == two[0]
                            or one.startswith(two)
                            or two.startswith(one)
                        )
                        for one in given[person]
                        for two in given[other]
                    )
                    if (
                        set(dates[person]) & set(dates[other])
                        or rival
                        or strangers
                        or len(signed) > 1
                        or len(births) > 1
                        or posthumous
                    ):
                        blocked.append((person, other))
                        continue
                    if min(abs((a - b).days) for a in people[person] for b in people[other]) <= NEAR:
                        considered.update((person, other))
                        parent[root(person)] = root(other)

        groups: dict[UUID, list[UUID]] = defaultdict(list)
        for person in considered:
            groups[root(person)].append(person)
        refused = {root(a) for a, b in blocked if a in parent and b in parent and root(a) == root(b)}
        merged = skipped = 0
        for key, people in groups.items():
            schemes = defaultdict(set)
            for person in people:
                for scheme, values in held[person].items():
                    schemes[scheme] |= values
            if key in refused or any(len(values) > 1 for values in schemes.values()):
                skipped += 1
                continue
            survivor = max(
                people,
                key=lambda person: (
                    any(scheme in held[person] for scheme in STRONG),
                    len(dates[person]),
                    -created[person].timestamp(),
                    -person.int,
                ),
            )
            for person in people:
                if person != survivor:
                    cs.merge(person, survivor)
                    merged += 1
        print(f"{len(groups)} groups, {merged} merged, {skipped} skipped, deferred pipelines: {sorted(busy)}")

        if not {"gb.byelections", "gb.umit"} <= busy:
            lineage: dict[UUID, UUID] = {}

            def party_root(node: UUID) -> UUID:
                lineage.setdefault(node, node)
                while lineage[node] != node:
                    lineage[node] = lineage[lineage[node]]
                    node = lineage[node]
                return node

            def fold(text: str) -> str:
                return " ".join(re.findall(r"[a-z]+", re.sub(r"\([^)]*\)", " ", text.lower()).replace("&", "and")))

            for left, right in conn.execute(
                """select coalesce(a.into_id, r.from_party_id), coalesce(b.into_id, r.to_party_id) from party_relation r
                   left join entity_merge a on a.entity_id = r.from_party_id
                   left join entity_merge b on b.entity_id = r.to_party_id
                   where r.kind in ('merged_into', 'successor_of', 'refounded_as', 'absorbed_by')"""
            ).fetchall():
                lineage[party_root(left)] = party_root(right)
            linked: dict[UUID, set[UUID]] = defaultdict(set)
            for left, right in conn.execute(
                """select a.id, b.id from crosswalk c join boundary_set s on s.id = c.to_boundary_set_id
                   join crosswalk_weight w on w.crosswalk_id = c.id
                   join area a on a.k = w.from_k join area b on b.k = w.to_k
                   where s.scheme_code = 'gb.pcon' and w.weight > 0"""
            ).fetchall():
                linked[left].add(right)
                linked[right].add(left)
            seated: dict[UUID, str] = {
                area: fold(text)
                for area, text in conn.execute(
                    """select n.entity_id, n.text from name n join area a on a.id = n.entity_id
                       where a.scheme_code = 'gb.pcon' and n.is_preferred"""
                ).fetchall()
            }
            races: dict[UUID, set[tuple[UUID, date]]] = defaultdict(set)
            seats: dict[UUID, list[tuple[date, UUID | None, UUID | None]]] = defaultdict(list)
            wins: dict[UUID, set[tuple[date, str]]] = defaultdict(set)
            for person, polity, day, area, party, won in conn.execute(
                """select coalesce(m.into_id, cy.person_id), coalesce(u.polity_id, e.polity_id), e.date_from,
                          k.district_area_id, coalesce(p.into_id, o.party_id),
                          exists (select from outcome t where t.option_id = o.id and t.kind in ('won', 'elected')
                                  and (t.candidacy_id is null or t.candidacy_id = cy.id))
                   from candidacy cy
                   left join entity_merge m on m.entity_id = cy.person_id
                   join contest_option o on o.id = cy.option_id join contest k on k.id = o.contest_id
                   join election e on e.id = k.election_id left join election u on u.id = e.parent_id
                   left join entity_merge p on p.entity_id = o.party_id
                   where e.date_from is not null and e.polity_id in (
                     select distinct e.polity_id from election e join changeset c on c.id = e.changeset_id
                     where c.pipeline = 'gb.byelections')"""
            ).fetchall():
                races[person].add((polity, day))
                seats[person].append((day, area, None if party is None else party_root(party)))
                if won and area in seated:
                    wins[person].add((day, seated[area]))
            titles = re.compile(r"^((sir|dr|rev|hon|lord|lady|dame|col|capt|maj|gen|mr|mrs|miss)\.? )+", re.IGNORECASE)
            known: dict[tuple[str, frozenset[str]], set[UUID]] = defaultdict(set)
            for person, text in conn.execute(
                """with holder as (
                     select distinct coalesce(m.into_id, x.entity_id) as person from external_id x
                     left join entity_merge m on m.entity_id = x.entity_id
                     where x.scheme in ('gb.umit.person', 'mnis', 'wikidata'))
                   select h.person, n.text from holder h join name n on n.entity_id = h.person
                   union all
                   select h.person, n.text from holder h join entity_merge m on m.into_id = h.person
                   join name n on n.entity_id = m.entity_id"""
            ).fetchall():
                if person in races and (named := words(titles.sub("", text))) is not None:
                    known[named].add(person)

            def apart(person: UUID, other: UUID) -> bool:
                return (
                    bool(races[person] & races[other])
                    or not any(
                        polity == theirs and abs((day - when).days) <= NEAR
                        for polity, day in races[person]
                        for theirs, when in races[other]
                    )
                    or (person in born and other in born and born[person] != born[other])
                    or any(
                        one in died and min(day for _, day in races[two]) > died[one] + timedelta(days=365)
                        for one, two in ((person, other), (other, person))
                    )
                )

            def nearest_party(person: UUID, other: UUID) -> bool:
                pair = min(
                    ((mine, theirs) for mine in seats[person] for theirs in seats[other]),
                    key=lambda pair: abs((pair[0][0] - pair[1][0]).days),
                )
                return pair[0][2] is not None and pair[0][2] == pair[1][2]

            if "gb.umit" not in busy:
                held_ids: dict[UUID, dict[str, set[str]]] = defaultdict(lambda: defaultdict(set))
                for person, scheme, value in conn.execute(
                    """select coalesce(m.into_id, x.entity_id), x.scheme, x.value from external_id x
                       left join entity_merge m on m.entity_id = x.entity_id
                       where x.scheme in ('gb.umit.person', 'mnis', 'wikidata')"""
                ).fetchall():
                    held_ids[person][scheme].add(value.strip('"'))
                terms: dict[str, set[tuple[date, str]]] = defaultdict(set)
                for row in json.loads(raw.cached(commons.SOURCE, commons.SERVICE).path.read_text(encoding="utf-8"))[
                    "results"
                ]["bindings"]:
                    if "start" in row and "districtLabel" in row:
                        terms[row["item"]["value"].rsplit("/", 1)[1]].add(
                            (date.fromisoformat(row["start"]["value"][:10]), fold(row["districtLabel"]["value"]))
                        )
                stood: dict[str, set[tuple[date, str | None]]] = defaultdict(set)
                for row in json.loads(
                    raw.cached(wikidata_links.SOURCE, wikidata_links.CANDIDACIES).path.read_text(encoding="utf-8")
                )["results"]["bindings"]:
                    stood[row["item"]["value"].rsplit("/", 1)[1]].add(
                        (
                            date.fromisoformat(row["day"]["value"][:10]),
                            fold(row["districtLabel"]["value"]) if "districtLabel" in row else None,
                        )
                    )
                contested: dict[UUID, set[tuple[date, str]]] = defaultdict(set)
                for person, schemes in held_ids.items():
                    for number in schemes.get("mnis", ()):
                        try:
                            artifact = raw.cached(mnis.SOURCE, mnis.BIOGRAPHY.format(number))
                        except LookupError:
                            continue
                        biography = json.loads(artifact.path.read_text(encoding="utf-8")).get("value", {})
                        contested[person].update(
                            (date.fromisoformat(entry["startDate"][:10]), fold(entry["name"]))
                            for entry in [
                                *(biography.get("electionsContested") or []),
                                *(biography.get("representations") or []),
                            ]
                            if entry.get("startDate") and entry.get("house") == 1
                        )
                sittings: dict[UUID, list[tuple[date, date | None]]] = defaultdict(list)
                for person, start, end in conn.execute(
                    """select coalesce(m.into_id, t.person_id), t.from_date, t.to_date from tenure t
                       join changeset c on c.id = t.changeset_id left join entity_merge m on m.entity_id = t.person_id
                       where c.pipeline in ('gb.tenures', 'gb.members') and t.from_date is not null"""
                ).fetchall():
                    sittings[person].append((start, end))

                def ran(person: UUID) -> set[tuple[date, str]]:
                    return {
                        (day, seated[area]) for day, area, _ in seats[person] if area is not None and area in seated
                    }

                def signals(person: UUID, other: UUID) -> set[str]:
                    found: set[str] = set()
                    for one, two in ((person, other), (other, person)):
                        items = held_ids[one].get("wikidata", set())
                        if any(
                            abs((day - start).days) <= 60 and seat == district
                            for item in items
                            for start, district in terms.get(item, ())
                            for day, seat in wins[two]
                        ):
                            found.add("wikidata term")
                        if any(
                            abs((day - when).days) <= 45 and district in (None, seat)
                            for item in items
                            for when, district in stood.get(item, ())
                            for day, seat in ran(two)
                        ):
                            found.add("wikidata candidacy")
                        if any(
                            abs((day - when).days) <= 3 and seat == place
                            for when, place in contested.get(one, ())
                            for day, seat in ran(two)
                        ):
                            found.add("mnis")
                        if any(
                            end is not None and 0 <= (start - end).days <= 60
                            for _, end in sittings[one]
                            for start, _ in sittings[two]
                        ):
                            found.add("tenure")
                    return found

                umit_root: dict[UUID, UUID] = {}

                def umit_find(node: UUID) -> UUID:
                    umit_root.setdefault(node, node)
                    while umit_root[node] != node:
                        umit_root[node] = umit_root[umit_root[node]]
                        node = umit_root[node]
                    return node

                edges: Counter[str] = Counter()
                hard: set[UUID] = set()
                soft: list[tuple[UUID, UUID]] = []
                for key, everyone in known.items():
                    people = sorted(person for person in everyone if held_ids[person].get("gb.umit.person"))
                    for index, person in enumerate(people):
                        for other in people[index + 1 :]:
                            if apart(person, other) or any(
                                held_ids[person].get(scheme)
                                and held_ids[other].get(scheme)
                                and held_ids[person][scheme] != held_ids[other][scheme]
                                for scheme in ("mnis", "wikidata")
                            ):
                                continue
                            if found := signals(person, other):
                                edges.update(found)
                                hard.update((person, other))
                                umit_root[umit_find(person)] = umit_find(other)
                            elif (
                                len(key[0].split()) >= 3
                                and any(
                                    area is not None and (area == seat or seat in linked[area])
                                    for _, area, _ in seats[person]
                                    for _, seat, _ in seats[other]
                                )
                                and nearest_party(person, other)
                            ):
                                soft.append((person, other))
                ends = Counter(person for pair in soft for person in pair)
                for person, other in soft:
                    if person not in hard and other not in hard and ends[person] == ends[other] == 1:
                        edges["soft"] += 1
                        umit_root[umit_find(person)] = umit_find(other)
                clusters: dict[UUID, set[UUID]] = defaultdict(set)
                for person in list(umit_root):
                    clusters[umit_find(person)].add(cs.survivors.get(person, person))
                umit_joined = umit_refused = 0
                for group in clusters.values():
                    if len(group) < 2:
                        continue
                    if any(
                        len({value for person in group for value in held_ids[person].get(scheme, ())}) > 1
                        for scheme in ("mnis", "wikidata")
                    ) or any(races[one] & races[two] for one in group for two in group if one != two):
                        umit_refused += 1
                        continue
                    survivor = max(
                        group,
                        key=lambda person: (
                            any(scheme in held_ids[person] for scheme in ("mnis", "wikidata")),
                            len(races[person]),
                            -created[person].timestamp(),
                            -person.int,
                        ),
                    )
                    for person in group:
                        if person != survivor:
                            for row in cs.rows.get("entity_merge", []):
                                if row["into_id"] == person and isinstance(row["entity_id"], UUID):
                                    row["into_id"] = survivor
                                    cs.survivors[row["entity_id"]] = survivor
                                    cs.merged.setdefault(str(survivor), []).append(str(row["entity_id"]))
                            cs.merged.pop(str(person), None)
                            cs.merge(person, survivor)
                            races[survivor] |= races[person]
                            seats[survivor] += seats[person]
                            umit_joined += 1
                print(
                    f"{umit_joined} Umit records joined across seats ({dict(edges)}), "
                    f"{umit_refused} groups refused for conflicting ids or races"
                )

            if "gb.byelections" not in busy:
                alone: dict[UUID, set[tuple[str, frozenset[str]]]] = defaultdict(set)
                for person, text in conn.execute(
                    """select x.entity_id, n.text from external_id x join name n on n.entity_id = x.entity_id
                       where x.scheme = 'gb.byelections.person'
                         and not exists (select from entity_merge m where m.entity_id = x.entity_id)
                         and not exists (select from external_id y where y.entity_id = x.entity_id and y.scheme <> x.scheme)"""
                ).fetchall():
                    if (named := words(titles.sub("", text))) is not None:
                        alone[person].add(named)
                joined = doubtful = weak = 0
                for person, keys in alone.items():
                    if person in cs.survivors:
                        continue
                    found = {
                        cs.survivors.get(other, other)
                        for key in keys
                        for other in known.get(key, ())
                        if other != person and not apart(person, other)
                    }
                    if len(found) > 1:
                        doubtful += 1
                        continue
                    if not found:
                        continue
                    other = found.pop()
                    if (
                        any(
                            len(key[0].split()) >= 3
                            for key in keys
                            if other in {cs.survivors.get(one, one) for one in known.get(key, ())}
                        )
                        or any(
                            area is not None and (area == seat or seat in linked[area])
                            for _, area, _ in seats[person]
                            for _, seat, _ in seats[other]
                        )
                        or nearest_party(person, other)
                    ):
                        cs.merge(person, other)
                        joined += 1
                    else:
                        weak += 1
                print(
                    f"{joined} by-election people joined to their other races, {weak} with too little evidence, "
                    f"{doubtful} with more than one match"
                )

        parties: dict[UUID, tuple[str, int, bool, set[str]]] = {
            party: (polity, options, linked, set(writers))
            for party, polity, options, linked, writers in conn.execute(
                """select p.id, p.polity_id::text, (select count(*) from contest_option o where o.party_id = p.id),
                          exists (select from external_id x where x.entity_id = p.id and x.scheme = 'wikidata'),
                          array_remove(array[c.pipeline] || array(select distinct w.pipeline from contest_option o
                                       join changeset w on w.id = o.changeset_id where o.party_id = p.id), null)
                   from party p join changeset c on c.id = p.changeset_id
                   where p.id = any(%s) and not exists (select from entity_merge m where m.entity_id = p.id)""",
                [[UUID(party) for group in PARTIES.values() for party in group]],
            ).fetchall()
        }
        for label, group in PARTIES.items():
            members = [UUID(party) for party in group]
            if not all(party in parties for party in members):
                print(f"{label}: already merged or missing")
                continue
            if len({parties[party][0] for party in members}) > 1:
                raise ValueError(f"{label}: parties of different polities")
            if any(parties[party][3] & busy for party in members):
                print(f"{label}: deferred")
                continue
            survivor = max(members, key=lambda party: (parties[party][2], parties[party][1]))
            for party in members:
                if party != survivor:
                    cs.merge(party, survivor)
        nuances: dict[str, list[tuple[UUID, str | None, int]]] = defaultdict(list)
        for party, text, item, options in conn.execute(
            """select p.id, n.text,
                      (select x.value from external_id x where x.entity_id = p.id and x.scheme = 'wikidata' limit 1),
                      (select count(*) from contest_option o where o.party_id = p.id)
               from party p join changeset c on c.id = p.changeset_id
               join name n on n.entity_id = p.id and n.is_preferred
               where c.pipeline in ('fr.cdsp', 'fr.interieur')
                 and not exists (select from entity_merge m where m.entity_id = p.id)"""
        ).fetchall():
            folded = "".join(ch for ch in unicodedata.normalize("NFKD", text) if not unicodedata.combining(ch))
            nuances[folded.lower().rstrip("s")].append((party, item, options))
        for members in nuances.values():
            if len(members) < 2 or len({item for _, item, _ in members if item}) > 1:
                continue
            survivor = max(members, key=lambda member: (member[1] is not None, member[2]))[0]
            for party, _, _ in members:
                if party != survivor:
                    cs.merge(party, survivor)
        for label, keys in PEOPLE.items():
            found = [
                person
                for scheme, value in keys
                for (person,) in conn.execute(
                    """select x.entity_id from external_id x where x.scheme = %s and x.value = %s
                       and not exists (select from entity_merge m where m.entity_id = x.entity_id)""",
                    [scheme, value],
                ).fetchall()
            ]
            if len(found) != len(keys):
                print(f"{label}: already merged or missing")
            for person in dict.fromkeys(found[1:]):
                if person != found[0] and person not in cs.survivors:
                    cs.merge(person, found[0])
        if "gb.tenures" not in busy:
            sitting = 0
            for winner, member in conn.execute(
                """select distinct cy.person_id, t.person_id
                   from tenure t join changeset w on w.id = t.changeset_id and w.pipeline = 'gb.tenures'
                   join contest c on c.id = t.contest_id and c.magnitude = 1
                   join election e on e.id = c.election_id and e.kind = 'by_election'
                   join contest_option o on o.contest_id = c.id
                   join candidacy cy on cy.option_id = o.id and cy.person_id is not null
                   join outcome x on x.option_id = o.id and x.kind in ('elected', 'won')
                     and (x.candidacy_id is null or x.candidacy_id = cy.id)
                   where cy.person_id <> t.person_id
                     and not exists (select from entity_merge m where m.entity_id in (cy.person_id, t.person_id))
                     and not exists (
                       select from external_id a join external_id b on b.scheme = a.scheme and b.value <> a.value
                       where a.entity_id = cy.person_id and b.entity_id = t.person_id
                         and a.scheme in ('mnis', 'wikidata', 'bioguide')
                     )
                     and not exists (
                       select from tenure a join tenure b on b.person_id = t.person_id
                         and daterange(a.from_date, a.to_date) && daterange(b.from_date, b.to_date)
                         and a.seat_id is distinct from b.seat_id
                       where a.person_id = cy.person_id
                     )"""
            ).fetchall():
                winner, member = cs.survivors.get(winner, winner), cs.survivors.get(member, member)
                if winner != member and winner not in cs.survivors:
                    cs.merge(winner, member)
                    sitting += 1
            print(f"{sitting} by-election winners joined to the member whose tenure the by-election began")
        with open(Path(__file__).with_name("identity_bioguide.tsv"), newline="") as twins:
            for twin in csv.DictReader(twins, delimiter="\t"):
                member = conn.execute(
                    """select coalesce(m.into_id, x.entity_id) from external_id x
                       left join entity_merge m on m.entity_id = x.entity_id
                       where x.scheme in ('bioguide', 'wikidata') and x.value = %s limit 1""",
                    [twin["strong"]],
                ).fetchone()
                person = UUID(twin["person"])
                target = None if member is None else cs.survivors.get(member[0], member[0])
                if (
                    target is None
                    or target == person
                    or person in cs.survivors
                    or conn.execute("select from entity_merge where entity_id = %s", [person]).fetchone() is not None
                ):
                    print(f"{twin['name']} ({twin['strong']}): already merged or missing")
                    continue
                for row in cs.rows.get("entity_merge", []):
                    absorbed = row["entity_id"]
                    if row["into_id"] == person and isinstance(absorbed, UUID):
                        row["into_id"] = target
                        cs.survivors[absorbed] = target
                        cs.merged.setdefault(str(target), []).append(str(absorbed))
                cs.merged.pop(str(person), None)
                cs.merge(person, target)
        columns = ("id", "entity_id", "text", "lang", "script", "kind", "source_id")
        with open(Path(__file__).with_name("identity_names.tsv"), newline="") as curated:
            for entry in csv.DictReader(curated, delimiter="\t"):
                stored = conn.execute(
                    """select id, entity_id, text, lang, script, kind, source_id from name
                       where entity_id = %s and lang = 'en' and valid_from is null and valid_to is null
                       order by text = %s desc, is_preferred desc, id""",
                    [entry["entity"], entry["text"]],
                ).fetchall()
                for index, row in enumerate(stored):
                    cs.add(
                        "name",
                        **dict(zip(columns, row, strict=True)),
                        is_preferred=index == 0 and row[2] == entry["text"],
                    )
                if not stored or stored[0][2] != entry["text"]:
                    cs.add(
                        "name",
                        id=None,
                        entity_id=UUID(entry["entity"]),
                        text=entry["text"],
                        lang="en",
                        script=None,
                        kind="official",
                        source_id=None,
                        is_preferred=True,
                    )
                if (county := re.fullmatch(r"(.+) \(([^)]+)\), [A-Z]{2}", entry["text"])) is None:
                    continue
                place = f"{county[1]} ({county[2]})"
                for row in conn.execute(
                    """select id, entity_id, text, lang, script, kind, source_id from name
                       where is_preferred and lang = 'en' and valid_from is null and valid_to is null
                         and strpos(text, %s) > 0 and strpos(text, %s) = 0
                         and entity_id in (select id from contest where district_area_id = %s
                                           union all select id from election where polity_id in (
                                            select polity_id from polity_territory where area_id = %s))""",
                    [county[1], place, entry["entity"], entry["entity"]],
                ).fetchall():
                    cs.add("name", **dict(zip(columns, row, strict=True)), is_preferred=False)
                    cs.add(
                        "name",
                        id=None,
                        entity_id=row[1],
                        text=row[2].replace(county[1], place, 1),
                        lang=row[3],
                        script=row[4],
                        kind=row[5],
                        source_id=row[6],
                        is_preferred=True,
                    )
        for row in conn.execute(
            """select n.id, n.entity_id, n.text, n.lang, n.script, n.kind, n.source_id from (
                 select n.*, row_number() over (
                   partition by n.entity_id, n.lang, n.script
                   order by c.pipeline = o.pipeline desc, n.recorded_from, n.id) as rank
                 from name n join changeset c on c.id = n.changeset_id
                 join entity e on e.id = n.entity_id join changeset o on o.id = e.changeset_id
                 where n.is_preferred and n.valid_from is null and n.valid_to is null
                   and not exists (select from entity_merge m where m.entity_id = n.entity_id)
               ) n where n.rank > 1""",
        ).fetchall():
            cs.add(
                "name",
                **dict(zip(("id", "entity_id", "text", "lang", "script", "kind", "source_id"), row, strict=True)),
                is_preferred=False,
            )
        columns = ("id", "entity_id", "text", "lang", "script", "kind", "source_id")
        staged = {row["id"] for row in cs.rows.get("name", []) if row.get("id") is not None}
        assembly = 0
        for row in conn.execute(
            """select p.id, p.entity_id, p.text, p.lang, p.script, p.kind, p.source_id,
                      array_agg(m.id order by m.recorded_from, m.id), array_agg(m.text order by m.recorded_from, m.id)
               from name p join entity e on e.id = p.entity_id and e.type = 'person'
               join name m on m.entity_id = p.entity_id and m.id <> p.id and m.lang = p.lang and m.text ~ ' '
                 and m.text <> p.text and m.valid_from is null and m.valid_to is null
               join changeset c on c.id = m.changeset_id and c.pipeline = 'gb.ni_mlas'
               where p.is_preferred and p.valid_from is null and p.valid_to is null
                 and not exists (select from entity_merge x where x.entity_id = p.entity_id)
               group by p.id""",
        ).fetchall():
            tokens = row[2].split()
            fits = {
                text: name
                for name, text in zip(row[7], row[8], strict=True)
                if len(text.split()) <= len(tokens) and text.split()[-1].lower() == tokens[-1].lower()
            }
            shortest = [text for text in fits if len(text.split()) == min((len(t.split()) for t in fits), default=0)]
            if len(shortest) != 1 or row[0] in staged or fits[shortest[0]] in staged:
                continue
            chosen = conn.execute(
                "select id, entity_id, text, lang, script, kind, source_id from name where id = %s",
                [fits[shortest[0]]],
            ).fetchone()
            assert chosen is not None
            cs.add("name", **dict(zip(columns, row[:7], strict=True)), is_preferred=False)
            cs.add("name", **dict(zip(columns, chosen, strict=True)), is_preferred=True)
            assembly += 1
        print(f"{assembly} Northern Ireland Assembly members take the Assembly's own name for them")
        fuller: set[UUID] = set()
        for row in conn.execute(
            """select distinct on (p.id) p.id, p.entity_id, p.text, p.lang, p.script, p.kind, p.source_id,
                      f.id, f.entity_id, f.text, f.lang, f.script, f.kind, f.source_id
               from name p join entity e on e.id = p.entity_id and e.type = 'person'
               join name f on f.entity_id = p.entity_id and f.lang = p.lang and not f.is_preferred
                 and f.valid_from is null and f.valid_to is null and f.text ~ ' ' and f.text ilike '%% ' || p.text
               where p.is_preferred and p.text !~ ' ' and p.valid_from is null and p.valid_to is null
               order by p.id, length(f.text) desc""",
        ).fetchall():
            cs.add("name", **dict(zip(columns, row[:7], strict=True)), is_preferred=False)
            cs.add("name", **dict(zip(columns, row[7:], strict=True)), is_preferred=True)
            fuller.add(row[1])
        accented = 0
        for row in conn.execute(
            """select distinct on (p.id) p.id, p.entity_id, p.text, p.lang, p.script, p.kind, p.source_id,
                      f.id, f.entity_id, f.text, f.lang, f.script, f.kind, f.source_id
               from name p join entity e on e.id = p.entity_id and e.type = 'person'
               join changeset c on c.id = p.changeset_id
               join name f on f.entity_id = p.entity_id and f.id <> p.id and f.lang = p.lang and not f.is_preferred
                 and f.script is not distinct from p.script and f.valid_from is null and f.valid_to is null
                 and f.text <> p.text and unaccent(f.text) = p.text and f.text !~ '[’‘“”ıİ]'
               join changeset w on w.id = f.changeset_id
               where p.is_preferred and p.valid_from is null and p.valid_to is null and p.text = unaccent(p.text)
                 and split_part(c.pipeline, '.', 1) in ('fr', 'gb', 'ie')
                 and split_part(w.pipeline, '.', 1) = split_part(c.pipeline, '.', 1)
                 and not exists (select from entity_merge m where m.entity_id = p.entity_id)
               order by p.id, f.recorded_from, f.id""",
        ).fetchall():
            if row[1] in fuller:
                continue
            cs.add("name", **dict(zip(columns, row[:7], strict=True)), is_preferred=False)
            cs.add("name", **dict(zip(columns, row[7:], strict=True)), is_preferred=True)
            fuller.add(row[1])
            accented += 1
        print(f"{accented} preferred names take a same-polity source's accents")
        recased = 0
        for row in conn.execute(
            """select p.id, p.entity_id, p.text, p.lang, p.script, p.kind, p.source_id,
                      array_agg(distinct f.text) filter (where w.pipeline = 'wikidata_people'),
                      array_agg(distinct f.text)
               from name p join entity e on e.id = p.entity_id and e.type = 'person'
               join name f on f.entity_id = p.entity_id and f.id <> p.id and lower(f.text) = lower(p.text)
                 and f.text <> p.text and f.valid_from is null and f.valid_to is null
               join changeset w on w.id = f.changeset_id
               where p.is_preferred and p.valid_from is null and p.valid_to is null
                 and p.text ~ '[^ ] ([Dd]e|[Vv]an|[Vv]on|[Ll]a) [^ ]'
                 and not exists (select from entity_merge m where m.entity_id = p.entity_id)
               group by p.id""",
        ).fetchall():
            if row[1] in fuller:
                continue
            plain = names.PARTICLE.sub(lambda match: match[0].lower(), row[2])
            spellings = {
                text
                for text in row[7] or row[8]
                if names.PARTICLE.sub(lambda match: match[0].lower(), text) == plain
                and names.PARTICLE.findall(text) != names.PARTICLE.findall(row[2])
                and (row[7] is not None or text == plain)
            }
            if len(spellings) != 1:
                continue
            cs.add("name", **dict(zip(columns, row[:7], strict=True)), is_preferred=False)
            cs.add(
                "name",
                **dict(zip(columns, row[:7], strict=True)) | {"id": None, "text": spellings.pop()},
                is_preferred=True,
            )
            recased += 1
        print(f"{recased} preferred names take a particle's case from a source's own spelling")
        staged = {row["id"] for row in cs.rows.get("name", []) if row.get("id") is not None}
        initialed: set[tuple[UUID, str, str | None]] = set()
        for name, entity, text, lang, script, kind, source, pipeline in conn.execute(
            """select n.id, n.entity_id, n.text, n.lang, n.script, n.kind, n.source_id, c.pipeline from name n
               join entity e on e.id = n.entity_id and e.type = 'person'
               join changeset c on c.id = n.changeset_id
               where n.is_preferred and n.valid_from is null and n.valid_to is null
                 and (n.text ~ '(^|[[:space:]])[A-Z][[:space:]]'
                      or n.text ~* '^(the|rt|right|hon|honou?rable|rev|revd|reverend|very|mr|mrs|ms|miss|mx|dr|sir|dame|prof|professor)[.]?[[:space:]]')"""
        ).fetchall():
            if entity in fuller or name in staged:
                continue
            bare = HONORIFICS.sub("", text)
            if len(bare.split()) < 2 or (
                re.match(r"(?i)(mrs|miss)\b", text) is not None
                and (pipeline.startswith("us.") or pipeline == "identity")
            ):
                bare = text
            if (fixed := names.initials(bare)) == text:
                continue
            cs.add(
                "name",
                id=name,
                entity_id=entity,
                text=text,
                lang=lang,
                script=script,
                kind=kind,
                source_id=source,
                is_preferred=False,
            )
            if (entity, lang, script) in initialed:
                continue
            initialed.add((entity, lang, script))
            cs.add(
                "name",
                entity_id=entity,
                text=fixed,
                lang=lang,
                script=script,
                kind=kind,
                source_id=source,
                is_preferred=True,
            )
        staged = {row["id"] for row in cs.rows.get("name", []) if row.get("id") is not None}
        displayed = Counter()
        for row in conn.execute(
            """select n.id, n.entity_id, n.text, n.lang, n.script, n.kind, n.source_id, c.pipeline,
                      (select w.text from name w join changeset x on x.id = w.changeset_id
                       where w.entity_id = n.entity_id and x.pipeline = 'wikidata_people'
                         and lower(w.text) = lower(n.text) and w.text <> n.text limit 1)
               from name n join entity e on e.id = n.entity_id and e.type = 'person'
               join changeset c on c.id = n.changeset_id
               where n.is_preferred and n.valid_from is null and n.valid_to is null
                 and n.text ~ '[[:upper:]]{2}'
                 and not exists (select from entity_merge m where m.entity_id = n.entity_id)""",
        ).fetchall():
            if row[0] in staged or row[1] in fuller or (row[1], row[3], row[4]) in initialed:
                continue
            if (fixed := names.display(row[2], row[3], row[8])) == row[2]:
                continue
            cs.add("name", **dict(zip(columns, row[:7], strict=True)), is_preferred=False)
            cs.add("name", **dict(zip(columns, row[:7], strict=True)) | {"id": None, "text": fixed}, is_preferred=True)
            displayed[row[7]] += 1
        print(f"{displayed.total()} preferred names in capitals take mixed case: {dict(displayed.most_common())}")
        return cs.write()
