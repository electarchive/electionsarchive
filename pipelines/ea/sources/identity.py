import re
import unicodedata
from collections import defaultdict
from datetime import date, datetime
from itertools import pairwise
from pathlib import Path
from uuid import UUID

from ea.changeset import Changeset
from ea.store import connect

SOURCE = "identity"
VERSION = "1"
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
}
PLACEHOLDERS = re.compile(
    r"\b(unknown|scatter\w*|write[- ]?ins?|all other|others?|blank|void|none of|uncommitted|vacan\w*|no candidate)\b"
)


def words(text: str) -> tuple[str, frozenset[str]] | None:
    folded = "".join(
        c for c in unicodedata.normalize("NFKD", text.lower().replace("ı", "i")) if not unicodedata.combining(c)
    )
    folded = re.sub(r"\([^)]*\)|\"[^\"]*\"|“[^”]*”", " ", folded)
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
            "select entity_id, scheme, value from external_id where scheme = any(%s)", [list(IDENTITIES)]
        ).fetchall():
            held[person].setdefault(scheme, set()).add(value)
        created: dict[UUID, datetime] = dict(
            conn.execute("select id, created_at from entity where type = 'person'").fetchall()
        )
        born: dict[UUID, date] = dict(
            conn.execute("select id, birth_date from person where birth_date is not null").fetchall()
        )
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "People recorded once per source or per race joined into one person where the same printed name "
                "(ignoring case, accents, and middle initials) ran for the same office or body in the same "
                "district within 12 years, and nothing tells them apart, and party labels of one state or country "
                "that sources spelled apart joined by hand"
            ),
            polity=None,
            kind="match",
        )

        dates: dict[UUID, list[date]] = defaultdict(list)
        pipelines: dict[UUID, set[str]] = defaultdict(set)
        lines: dict[tuple[object, ...], dict[UUID, list[date]]] = defaultdict(lambda: defaultdict(list))
        initials: dict[tuple[tuple[object, ...], UUID], set[frozenset[str]]] = defaultdict(set)
        for person, text, area, office, body, day, pipeline in rows:
            dates[person].append(day)
            pipelines[person].add(pipeline)
            if text is None or area is None or (office is None and body is None) or pipeline.startswith(MATCHED):
                continue
            named = words(text)
            if named is None:
                continue
            line = (area, office, body, named[0])
            lines[line][person].append(day)
            if named[1]:
                initials[line, person].add(named[1])

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
                    if set(dates[person]) & set(dates[other]) or len(signed) > 1 or len(births) > 1:
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
        return cs.write()
