"""Ethiopia's National Election Board (NEBE): the 2021 general election by constituency.

The Board's results page links, region by region, the Board-approved result form of every constituency for the House
of Peoples' Representatives and the regional state councils, one scanned page per constituency, for the polls of
21 June 2021 (approved 14 Sene 2013 E.C.) and 30 September 2021 (approved 20 Meskerem 2014 E.C.). Each form prints the
registered voters, the ballot papers received, used, spoiled, unused, valid, and invalid, and the votes of every
candidate. The English page links the same results as typed lists of the winners only, with their names in English.
"""

import json
import re
import subprocess
import unicodedata
from datetime import date
from functools import cache
from pathlib import Path
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "et.nebe_2021"
DRIVE = "https://drive.usercontent.google.com/download?id={}&export=download&confirm=t"
SITE = "https://nebe.org.et/sites/default/files"
PAGE_EN = "https://nebe.org.et/en/electionresult"
PAGE_AM = "https://nebe.org.et/am/electionresult"

# (poll, region, body): Drive file id of the scanned Board-approved forms (Amharic page).
FORMS: dict[tuple[str, str, str], str] = {
    ("2021-06-21", "addis-ababa", "hopr"): "1K8EZsHPloF5JYPBaG0IyoLD8rSiOmVCz",
    ("2021-06-21", "addis-ababa", "rc"): "1uM-5pcDGKoqNnNoB6W2hNb9K5YI0gPCS",
    ("2021-06-21", "afar", "hopr"): "1AWNcY_sZdSKbzVPsr9tYb0nQtq2dcz8T",
    ("2021-06-21", "afar", "rc"): "1g4ksDuk8KISP06RlDopr7L1hlW1Gry9V",
    ("2021-06-21", "amhara", "hopr"): "1UNN0Be3Hwz5YKqmm4s7HvhL7SOdMPeuF",
    ("2021-06-21", "amhara", "rc"): "1l5UMXM4_E_EfUJ_glqEjY2bXIgM5siJL",
    ("2021-06-21", "benishangul-gumuz", "hopr"): "13P3rRxp68k4Wrg59csmdgmQMEwy5KrES",
    ("2021-06-21", "benishangul-gumuz", "rc"): "18diVxmX7jHajR5fWbeISB4FAtoSx8Jdm",
    ("2021-06-21", "dire-dawa", "hopr"): "1gIxmzXW-QBgRK3kXSXSeoESdKeniRO88",
    ("2021-06-21", "dire-dawa", "rc"): "1qAbnTzzPqW9SOGRzwCkgJPEMTK-4sknd",
    ("2021-06-21", "gambela", "hopr"): "1HeQTEgQfA3rKi7bO4TXhGpMcNgNkwzSD",
    ("2021-06-21", "gambela", "rc"): "1SEvsjaLhi6-BLYdaTIx6U6RqMVkuTCYp",
    ("2021-06-21", "oromia", "hopr"): "1TNLnseS6yFPb9QLr8RXhOAG21plLyOKj",
    ("2021-06-21", "oromia", "rc"): "1FuIsqCjLrAFpgLOxxTz6tfCBi0XDQw5j",
    ("2021-06-21", "snnp", "hopr"): "1gg5PnhV6UQVAlm-JWcOTJaR0m1tV8nvU",
    ("2021-06-21", "snnp", "rc"): "1iSVTjDCAudcIewwkmfIHRfWyykfOsFRF",
    ("2021-06-21", "sidama", "hopr"): "16gMrX7iN-uy5FBlivGAZpt0Nn83vV1V0",
    ("2021-06-21", "sidama", "rc"): "1GLw8SJV1KCNhzJNvBhcDw5GQ1fHSkZBy",
    ("2021-09-30", "snnp", "hopr"): "1JqdH_TC7eGgdNR67_emzmD8uylTqpPio",
    ("2021-09-30", "snnp", "rc"): "1UvQwJsQaWng0arcKQGCeLzxpRqvGbJDs",
    ("2021-09-30", "dire-dawa", "rc"): "1McTr5t6MdZqoEj8j2n-94JSA2D50mKoX",
    ("2021-09-30", "harari", "hopr"): "11KAlN2p8EYYeg7kbRPXJrW7mCJKDBNk1",
    ("2021-09-30", "harari", "rc"): "14J5t_i1KrvGLGg1i49_rm2XfICSRmmWr",
    ("2021-09-30", "somali", "hopr"): "1Xdzdbp0hHon7q7yjFTktUOLZXRa5j18k",
    ("2021-09-30", "somali", "rc"): "1kg6yiOiNFLl5Fljeo2TKxvi_wmuRmz7u",
}
# (poll, region, body): typed list of the winners in English (English page).
WINNERS: dict[tuple[str, str, str], str] = {
    ("2021-06-21", "addis-ababa", "hopr"): DRIVE.format("1v08mgro21oDIcsQxv4E3E6h4cetOt_Fs"),
    ("2021-06-21", "addis-ababa", "rc"): DRIVE.format("1CZzoeQHUWsCSkjZ0jdHRgDAShH8QQxaB"),
    ("2021-06-21", "afar", "hopr"): DRIVE.format("1YWVm53oUtWRnZTGj3QIzpjYOSjalaFo-"),
    ("2021-06-21", "afar", "rc"): DRIVE.format("1YVDM1b55gSaozgCqpDINQ5fDxcpWby_k"),
    ("2021-06-21", "amhara", "hopr"): DRIVE.format("1vyjfHk0FKXmTbsc8BlHMV9S4YaFo5cy4"),
    ("2021-06-21", "amhara", "rc"): DRIVE.format("1dmiv10Ab1ZLs0OUo7CHV16sFz0WZODRT"),
    ("2021-06-21", "benishangul-gumuz", "hopr"): DRIVE.format("17G_0R4LsLIayP4zLEt0jnmdCs6AJ9Wfx"),
    ("2021-06-21", "benishangul-gumuz", "rc"): DRIVE.format("1Hrsk1e-dWjnq8nzIdjrhrimI6UVS8q5l"),
    ("2021-06-21", "dire-dawa", "hopr"): DRIVE.format("1kNWotyDKmO3ZfBGrZ6d8NoWXLYAEyhQy"),
    ("2021-06-21", "dire-dawa", "rc"): DRIVE.format("1iwwLrp6HWVfWsmDibWqhNCbIc1fGEUnF"),
    ("2021-06-21", "gambela", "hopr"): DRIVE.format("1GUXE-HsfU-ol9xSgvQH9R-crDO4IsN2F"),
    ("2021-06-21", "gambela", "rc"): DRIVE.format("1xfY7gvXEZjOYx2LBHflJ6bsonlKwAYbP"),
    ("2021-06-21", "oromia", "hopr"): DRIVE.format("1ZTi_LELntL4oKlZEMB8ZO-QHsrhmKTFs"),
    ("2021-06-21", "oromia", "rc"): DRIVE.format("13hBqMBpGlTMKEtL_mMw6mV87unngeCtV"),
    ("2021-06-21", "snnp", "hopr"): DRIVE.format("17qbsINjzleRuqHxfEupAW-4eaPRZ1vhP"),
    ("2021-06-21", "snnp", "rc"): DRIVE.format("12Fn7r1guULm3NVul8kg_PZjeLx8csS1t"),
    ("2021-06-21", "sidama", "hopr"): DRIVE.format("1wG8HDO-79rfmHi6zE1wMH7MwRAQnSBvH"),
    ("2021-06-21", "sidama", "rc"): DRIVE.format("1n3ecNh6h6rYTH_vjnCmmQ9yS_odFFZ9T"),
    ("2021-09-30", "snnp", "hopr"): DRIVE.format("1n4u6TIT7xo0F4ZGbdkAfXp1tJiOrKr6I"),
    ("2021-09-30", "snnp", "rc"): DRIVE.format("13CwTCpB90yFn1Ayug56Dv9n-ysPwjpMg"),
    ("2021-09-30", "dire-dawa", "rc"): DRIVE.format("1x7zaLc4TGepLE8vgp-FKHpem6ZjZmWrp"),
    ("2021-09-30", "harari", "hopr"): f"{SITE}/Harari%20HoPR%20Election%20Results%20Final%20-Sep%2030.pdf",
    ("2021-09-30", "harari", "rc"): DRIVE.format("1zb3OjtJ3_rz0WrqAf7cfF9YUXX75HDyX"),
    ("2021-09-30", "somali", "hopr"): f"{SITE}/Somali%20HoPR%20Election%20Results%20Final-Sep%2030.pdf",
    ("2021-09-30", "somali", "rc"): DRIVE.format("1ZipIRcsuR5y4EqaAcpjF2MCFmsgKEDLR"),
}
SUMMARY_HOPR = f"{SITE}/HOPR%20Last%20edition%20_0.JPG"
SUMMARY_RC = f"{SITE}/RC%20Last%20Edition.JPG"
SEPTEMBER = (
    f"{SITE}/Announcement%20of%20the%20Results%20of%20the%206th%20General%20Election%20Conducted%20on%20"
    "September%2030%20(2).pdf"
)


def form_url(poll: str, region: str, body: str) -> str:
    return DRIVE.format(FORMS[(poll, region, body)])


def fetch() -> None:
    raw.fetch(SOURCE, PAGE_EN, suffix=".html")
    raw.fetch(SOURCE, PAGE_AM, suffix=".html")
    for url in (SUMMARY_HOPR, SUMMARY_RC):
        raw.fetch(SOURCE, url, suffix=".jpg")
    raw.fetch(SOURCE, SEPTEMBER, suffix=".pdf")
    for url in WINNERS.values():
        raw.fetch(SOURCE, url, suffix=".pdf", check=_pdf)
    for poll, region, body in FORMS:
        raw.fetch(SOURCE, form_url(poll, region, body), suffix=".pdf", check=_pdf)


def _pdf(path: object) -> None:
    if not Path(str(path)).read_bytes()[:5] == b"%PDF-":
        raise ValueError(f"{path} is not a PDF")


ETHIOPIA = str(uuid5(package.NAMESPACE, "polity\x1fET"))
TRANSCRIPTION = Path(__file__).with_name("nebe_hopr_2021.json")
POLLS = {"2021-06-21": date(2021, 6, 21), "2021-09-30": date(2021, 9, 30)}
POLL_TEXT = {"2021-06-21": "ሰኔ 14 ቀን 2013 ዓ.ም.", "2021-09-30": "መስከረም 20 ቀን 2014 ዓ.ም."}
VOTES, REGISTERED, VALID, INVALID, VOTERS = 1, 2, 4, 5, 14
LINES = {0: REGISTERED, 2: VOTERS, 6: VALID, 7: INVALID}  # form lines 1, 3, 7, and 8
REGIONS: dict[str, tuple[str, str, str]] = {
    # key: (English, Amharic, language of the names printed on the region's forms)
    "addis-ababa": ("Addis Ababa", "አዲስ አበባ", "am"),
    "afar": ("Afar", "አፋር", "aa"),
    "amhara": ("Amhara", "አማራ", "am"),
    "benishangul-gumuz": ("Benishangul-Gumuz", "ቤኒሻንጉል ጉሙዝ", "am"),
    "dire-dawa": ("Dire Dawa", "ድሬዳዋ", "am"),
    "gambela": ("Gambela", "ጋምቤላ", "am"),
    "harari": ("Harari", "ሐረሪ", "am"),
    "oromia": ("Oromia", "ኦሮሚያ", "om"),
    "sidama": ("Sidama", "ሲዳማ", "am"),
    "snnp": ("Southern Nations, Nationalities, and Peoples", "ደቡብ ብሔር ብሔረሰቦችና ሕዝቦች", "am"),
    "somali": ("Somali", "ሶማሌ", "so"),
}
METHOD = (
    "Read from the Board-approved constituency result form, one scanned page per constituency, linked region by "
    "region from the Board's Amharic results page. Each number was read twice, by Tesseract OCR of each table cell "
    "and by a multimodal model reading the page (or by two different models where the cells could not be found), "
    "and kept only where the readings agree or a person settled them against the page. Registered voters are form "
    "line 1, voters are the signatures in the voter register (line 3), and valid and invalid ballots are lines 7 "
    "and 8. The form also prints the ballot papers received, unused, spoiled, found outside the box, and "
    "provisional (lines 2, 4, 5, 6, and 9), which are not stored here. The figures are stored as printed: on many "
    "forms the candidates' votes do not add up to the valid ballots, or the voters exceed the registered voters. "
    "Winners' names in English and their gender come from the Board's typed list of winners on its English page."
)
# Each party is matched from the party text on the form by these patterns, tried in order on the folded text.
PARTIES: list[tuple[str, str | None, str, str, str]] = [
    # key, English name, native name, language, pattern
    ("independent", None, "", "", r"የግል|independent|musharax madax|kaadhim"),
    ("pp", "Prosperity Party", "ብልፅግና ፓርቲ", "am", r"ብል[ፅጽ]ግ|bh?a+dh?a+dh|barwaaqo|leeda|prosperity"),
    (
        "ezema",
        "Ethiopian Citizens for Social Justice",
        "የኢትዮጵያ ዜጎች ለማኅበራዊ ፍትህ ፓርቲ",
        "am",
        r"[ዜዚ]ጎች|ኢዜማ|izema|lamm|caddaaladda",
    ),
    ("enat", "Enat Party", "እናት ፓርቲ", "am", r"እናት|harmee|haadhoo"),
    ("aeuo", "All Ethiopian Unity Organization", "የመላው ኢትዮጵያ አንድነት ድርጅት", "am", r"መላው"),
    ("nama", "National Movement of Amhara", "የአማራ ብሔራዊ ንቅናቄ", "am", r"አማራ ብ|saba amaara"),
    ("adfm", "Amhara Democratic Force Movement", "የአማራ ዴሞክራሲያዊ ኃይል ንቅናቄ", "am", r"አማራ ዴሞ"),
    (
        "efep",
        "Freedom and Equality Party",
        "ነፃነትና እኩልነት ፓርቲ",
        "am",
        r"ነ[ፃጻ]ነት(ና)? ?(ለ)?እኩልነት|bilisumm?aa? ?fi ?wa|sinaanta",
    ),
    ("hibir", "Hibir Ethiopia Democratic Party", "ኅብር ኢትዮጵያ ዴሞክራሲያዊ ፓርቲ", "am", r"[ኅህሕ][ብበ]ር ኢትዮጵያ"),
    ("esdp", "Ethiopian Social Democratic Party", "የኢትዮጵያ ሶሻል ዴሞክራቲክ ፓርቲ", "am", r"ሶሻል|sooshaal"),
    ("new-generation", "New Generation Party", "አዲስ ትውልድ ፓርቲ", "am", r"አዲስ ትውልድ|dhaloota"),
    ("eprp", "Ethiopian People's Revolutionary Party", "የኢትዮጵያ ሕዝባዊ አብዮታዊ ፓርቲ", "am", r"አብዮታዊ"),
    ("balderas", "Balderas for Genuine Democracy", "ባልደራስ ለእውነተኛ ዲሞክራሲ", "am", r"ባልደራስ"),
    ("onlf", "Ogaden National Liberation Front", "Jabhadda Waddaniga Xoreynta Ogaadeeniya", "so", r"jabhadda"),
    ("wsdp", "Western Somali Democratic Party", "Xisbiga Dimuqraadiga Soomaali Galbeed", "so", r"soomaali galbeed"),
    ("edu", "Ethiopian Democratic Union", "የኢትዮጵያ ዴሞክራቲክ ኅብረት", "am", r"ኢትዮጵያ ዴሞክራቲክ [ኅህ]"),
    ("sidama-unity", "Sidama Unity Party", "ሲዳማ አንድነት ፓርቲ", "am", r"ሲዳማ አንድነት"),
    (
        "sidama-pudo",
        "Sidama People's Unity Democratic Organization",
        "ሲዳማ ሕዝብ አንድነት ዴሞክራሲያዊ ድርጅት",
        "am",
        r"ሲዳማ ሕዝብ",
    ),
    ("hidase", "Hidase Party", "ኅዳሴ ፓርቲ", "am", r"[ኅህ]ዳሴ"),
    ("efp", "Ethiopian Freedom Party", "የኢትዮጵያ ነፃነት ፓርቲ", "am", r"ኢትዮጵያ ነ[ፃጻ]ነት ፓርቲ"),
    ("agew", "Agew National Council", "የአገው ብሔራዊ ሸንጎ", "am", r"አገው"),
    ("gedeo", "Gedeo People's Democratic Organization", "የጌዴኦ ሕዝብ ዴሞክራሲያዊ ድርጅት", "am", r"ጌዶ|ጌዴ|ጌሕዴድ"),
    ("wpdf", "Wolaita People's Democratic Front", "የወላይታ ሕዝብ ዴሞክራሲያዊ ግንባር", "am", r"[ዎዓዋወ]ላይታ ሕዝብ"),
    ("wnm", "Wolaita National Movement", "የወላይታ ብሔራዊ ንቅናቄ", "am", r"[ዎዓዋወ]ላይታ ብሔራዊ"),
    ("enup", "Ethiopian National Unity Party", "የኢትዮጵያ ብሔራዊ አንድነት ፓርቲ", "am", r"ብሔራዊ አንድነት"),
    ("qimant", "Qimant Democratic Party", "የቅማንት ዴሞክራሲያዊ ፓርቲ", "am", r"ቅማንት"),
    ("raya", "Raya Rayuma Democratic Party", "የራያ ራዩማ ዴሞክራሲያዊ ፓርቲ", "am", r"ራያ"),
    ("kafa-green", "Kafa Green Party", "ካፋ አረንጓዴ ፓርቲ", "am", r"ካ[ፋሩ] አረንጓዴ"),
    ("kafa-pdu", "Kafa Peoples' Democratic Union", "የካፋ ሕዝቦች ዴሞክራሲያዊ ኅብረት", "am", r"ካ[ፋሩ] ሕዝቦች"),
    ("gamo", "Gamo Democratic Party", "የጋሞ ዴሞክራሲያዊ ፓርቲ", "am", r"ጋሞ|ጋም ዴሞ"),
    ("kucha", "Kucha People's Democratic Party", "የቁጫ ሕዝብ ዴሞክራሲያዊ ፓርቲ", "am", r"[ቁቀ][ጫጮ] ሕዝብ"),
    ("olm", "Oromo Liberation Movement", "የኦሮሞ ነፃነት ንቅናቄ", "am", r"ኦሮሞ ነ[ፃጻ]ነት|bilisumma oromoo"),
    ("donga", "Donga People's Democratic Organization", "የዶንጋ ሕዝቦች ዴሞክራሲያዊ ድርጅት", "am", r"ዶንጋ"),
    ("argoba", "Argoba Nationality Democratic Movement", "የአርጎባ ብሔረሰብ ዴሞክራሲያዊ ንቅናቄ", "am", r"አርጎባ"),
    ("mocha", "Mocha Democratic Party", "ሞቻ ዴሞክራሲያዊ ፓርቲ", "am", r"ሞቻ"),
    ("gplm", "Gambella Peoples' Liberation Movement", "የጋምቤላ ሕዝቦች ነፃነት ንቅናቄ", "am", r"ጋምቤላ ሕዝቦች"),
    (
        "gpdlm",
        "Gambella People's Democratic Liberation Movement",
        "የጋምቤላ ሕዝብ ነፃነት ዴሞክራሲያዊ ንቅናቄ",
        "am",
        r"ጋምቤላ ሕዝብ ነ",
    ),
    ("wolene", "Wolene People's Democratic Party", "የወለኔ ሕዝብ ዴሞክራሲያዊ ፓርቲ", "am", r"ወለኔ"),
    ("goto", None, "የጎጦ ዴሞክራሲያዊ ፓርቲ", "am", r"ጎጦ"),
    ("hdo", "Harari Democratic Organization", "ሐረሪ ዴሞክራሲያዊ ድርጅት", "am", r"ሐረሪ"),
    ("bplm", "Benishangul People's Liberation Movement", "የቤኒሻንጉል ሕዝብ ነፃነት ንቅናቄ", "am", r"ቤ[ንኒ]ሻንጉል"),
    ("qafar-ummattah", None, "Qafar Ummattah Parti", "aa", r"qafar ummattah"),
    ("qafar-amobaxxaqah", None, "Qafar Amobaxxaqah Foocih Parti", "aa", r"qafar amob"),
    ("qafar-uguugumoh", None, "Qafar Uguugumoh Dimokrasih Inriinoh Fooca", "aa", r"qafar uguugumoh"),
    ("xaylok", None, "Ityoppiyah Xaylok Ayyuntiino Qadlih Parti", "aa", r"xaylok"),
    (
        "medrek",
        "Ethiopian Federal Democratic Unity Forum",
        "Itiyobbiyah Feedarali Ximokraasih Inkittion Madraaka",
        "aa",
        r"feedarali",
    ),
    ("qargubbi", None, "Qargubbi Agattiinah Ximokraasih Angoyyao", "aa", r"qargubbi"),
    ("cankah", None, "Cankah Faxita", "aa", r"cankah"),
    ("qusba", None, "Qusba Horihmissoyna Parti", "aa", r"qusba"),
    ("uummata-balaa", None, "Paartii Uummata Balaa Itiyoophiyaa", "om", r"u?umm?ata ball?aa?"),
    ("ilee-maa", None, "Paartii Ilee Maa", "om", r"ilee maa"),
]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


NEBE_SOURCE = key("source", "nebe_2021")
HOPR = key("body", "hopr")
FPTP = key("electoral_system", "hopr")


def fold(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text).casefold().replace("-", " ")).strip(" ()/")


def party_key(text: str) -> str | None:
    folded = fold(text)
    for code, _, _, _, pattern in PARTIES:
        if re.search(pattern, folded):
            return code
    return None


def party(code: str) -> str:
    return key("party", code)


def area(scheme: str, code: str) -> str:
    return key("area", scheme, code)


def packages() -> list[str]:
    return ["et.nebe_2021/geo", "et.nebe_2021/registry", *(f"et.nebe_2021/hopr-{poll}" for poll in POLLS)]


@cache
def forms() -> list[dict]:
    return json.loads(TRANSCRIPTION.read_text())["forms"]


@cache
def winners() -> dict[tuple[str, str], list[dict[str, str | int]]]:
    """The English page's typed lists of House winners, by poll and region."""
    found: dict[tuple[str, str], list[dict[str, str | int]]] = {}
    for (poll, region, body), url in WINNERS.items():
        if body != "hopr":
            continue
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout
        rows = found[(poll, region)] = []
        for line in text.splitlines():
            m = re.match(r"\s*(\d+)\s+(.+?)\s{2,}(.+?)\s{2,}(.+?)\s{2,}(Male|Female)\s+([\d,]+)\s*$", line)
            if m:
                rows.append(
                    {
                        "constituency": m[2].strip(),
                        "party": m[3].strip(),
                        "name": m[4].strip(),
                        "gender": m[5].lower(),
                        "votes": int(m[6].replace(",", "")),
                    }
                )
    return found


def code(form: dict) -> str:
    return f"{form['region']}:{changeset.slug(form['constituency_en'])}"


def parse() -> None:
    for p in (geo(), registry(), *(election(poll) for poll in POLLS)):
        p.write()


def emit() -> None:
    package.run("load", packages())


def geo() -> Package:
    p = Package(
        "et.nebe_2021/geo",
        SOURCE,
        "Ethiopia's regions and the House of Peoples' Representatives constituencies that voted in 2021, named as the "
        "National Election Board prints them, without boundaries",
    )
    p.external.add(ETHIOPIA)
    p.add(
        "scheme", code="et.region", polity_id=ETHIOPIA, kind="administrative", rank=1, noun="region", code_system=None
    )
    p.add(
        "scheme",
        code="et.hopr_constituency",
        polity_id=ETHIOPIA,
        kind="electoral_district",
        rank=2,
        noun="constituency",
        code_system=None,
    )
    for region, (english, amharic, _) in REGIONS.items():
        id = area("et.region", region)
        p.add("area", id=id, scheme_code="et.region")
        p.add("area_code", area_id=id, code=region)
        p.name(id, english, "en", "official")
        p.name(id, amharic, "am", "native")
    seen: set[str] = set()
    for form in forms():
        if code(form) in seen:
            continue
        seen.add(code(form))
        id = area("et.hopr_constituency", code(form))
        p.add("area", id=id, scheme_code="et.hopr_constituency")
        p.add("area_code", area_id=id, code=code(form))
        p.add("area_parent", area_id=id, parent_id=area("et.region", form["region"]))
        p.name(id, form["constituency_en"], "en", "official")
        if form.get("constituency") and form["constituency"] != form["constituency_en"]:
            p.name(
                id, form["constituency"], REGIONS[form["region"]][2] if _latin(form["constituency"]) else "am", "native"
            )
    return p


def _latin(text: str) -> bool:
    return not re.search(r"[ሀ-᎟]", text)


def registry() -> Package:
    p = Package(
        "et.nebe_2021/registry",
        SOURCE,
        "The House of Peoples' Representatives, its electoral system, and the parties on the 2021 House ballots, from "
        "the National Election Board of Ethiopia",
        depends=["et.nebe_2021/geo"],
    )
    p.external.add(ETHIOPIA)
    p.add(
        "source",
        id=NEBE_SOURCE,
        kind="official_body",
        publisher="National Election Board of Ethiopia",
        title="Election Result (የምርጫ ውጤት): Board-approved constituency result forms and lists of winners, 2021",
        url=PAGE_AM,
        license="Official publication; reuse terms are not stated",
        attribution="National Election Board of Ethiopia (የኢትዮጵያ ብሔራዊ ምርጫ ቦርድ)",
    )
    for url in (PAGE_EN, PAGE_AM, SUMMARY_HOPR, SUMMARY_RC, SEPTEMBER):
        p.artifact(raw.cached(SOURCE, url), NEBE_SOURCE)
    for (poll, region, body), url in WINNERS.items():
        if body == "hopr":
            p.artifact(raw.cached(SOURCE, url), NEBE_SOURCE)
    for poll, region, body in FORMS:
        if body == "hopr":
            p.artifact(raw.cached(SOURCE, form_url(poll, region, body)), NEBE_SOURCE)
    p.add("body", id=HOPR, polity_id=ETHIOPIA, kind="lower_house")
    p.name(HOPR, "House of Peoples' Representatives", "en", "official")
    p.name(HOPR, "የሕዝብ ተወካዮች ምክር ቤት", "am", "native")
    p.add("electoral_system", id=FPTP, polity_id=ETHIOPIA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", FPTP),
        entity_id=FPTP,
        kind="methodology",
        body_md="Each constituency elects one member, the candidate with the most votes (Proclamation 1162/2019, art. 4).",
        lang="en",
    )
    used = {party_key(c["party"]) for form in forms() for c in form["candidates"]}
    for code_, english, native, lang, _ in PARTIES:
        if code_ == "independent" or code_ not in used:
            continue
        id = party(code_)
        p.add("party", id=id, kind="party", polity_id=ETHIOPIA)
        if english:
            p.name(id, english, "en", "official")
        p.name(id, native, lang, "native" if english else "official")
    return p


def _artifact_id(url: str) -> str:
    stored = raw.cached(SOURCE, url)
    return str(uuid5(package.NAMESPACE, f"source_artifact\x1f{NEBE_SOURCE}\x1f{stored.storage_key}"))


def _put(p: Package, result_set: str, unit: str, option: str | None, measure: int, value: int | None) -> None:
    if value is None:
        return
    p.add(
        "result_value",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        flags=0,
        value=value,
    )


def _locate(
    p: Package, result_set: str, unit: str, option: str | None, measure: int, page: str, box: list[float]
) -> None:
    p.add(
        "value_locator",
        result_set_id=result_set,
        unit_id=unit,
        option_id=option,
        channel=0,
        measure=measure,
        count_round=0,
        page_id=page,
        source_id=None,
        x0=box[0],
        y0=box[1],
        x1=box[2],
        y1=box[3],
    )


def election(poll: str) -> Package:
    day = POLLS[poll]
    p = Package(
        f"et.nebe_2021/hopr-{poll}",
        SOURCE,
        f"The House of Peoples' Representatives election of {day:%-d %B %Y} by constituency, digitized from the "
        "National Election Board's scanned constituency result forms",
        depends=["et.nebe_2021/geo", "et.nebe_2021/registry"],
    )
    p.external.add(ETHIOPIA)
    for (poll_, region, body), url in WINNERS.items():
        if body == "hopr" and poll_ == poll:
            p.uses(raw.cached(SOURCE, url))
    for poll_, region, body in FORMS:
        if body == "hopr" and poll_ == poll:
            p.uses(raw.cached(SOURCE, form_url(poll_, region, body)))
    p.accept(
        "accounting.votes_valid",
        "The Board's forms print candidate votes that often do not add up to the valid ballots on line 7 (Addis Ababa "
        "constituency 4: 25,989 votes against 25,969 valid); both readings of every figure agree, so the printed "
        "figures are stored as they stand.",
    )
    if poll == "2021-06-21":
        p.accept(
            "accounting.turnout",
            "Two forms print more voter signatures (line 3) than registered voters (line 1): Welkite prints 10,064 "
            "registered and 88,022 voters, and Fungan Bira 318,108 registered and 318,267 voters; both are stored as "
            "printed.",
        )
    election_id = key("election", "hopr", poll)
    p.add(
        "election",
        id=election_id,
        polity_id=ETHIOPIA,
        kind="general",
        date_from=day,
        date_to=day,
        status="held",
        original_text=POLL_TEXT[poll],
    )
    path = f"et/{poll}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    suffix = "" if poll == "2021-06-21" else ", 30 September polls"
    p.name(election_id, f"2021 Ethiopian general election{suffix}", "en", "official")
    p.name(election_id, f"6ተኛው አጠቃላይ ምርጫ ({POLL_TEXT[poll]})", "am", "native")
    for form in (f for f in forms() if f["poll"] == poll):
        region = form["region"]
        tag = f"{poll}:{code(form)}"
        contest = key("contest", tag)
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="body_seats",
            body_id=HOPR,
            district_area_id=area("et.hopr_constituency", code(form)),
            magnitude=1,
            round_number=1,
            electoral_system_id=FPTP,
            default_result_kind="counted",
        )
        p.name(contest, form["constituency_en"], "en", "official")
        p.add(
            "slug",
            path=f"{path}/{region}-{changeset.slug(form['constituency_en'])}",
            entity_id=contest,
            is_current=True,
        )
        result_set = key("result_set", tag)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=NEBE_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        if form.get("caveat"):
            p.add(
                "note",
                id=key("note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md=form["caveat"],
                lang="en",
            )
        unit = key("unit", tag)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=ETHIOPIA,
            scheme_code="et.hopr_constituency",
            code=tag,
            area_relation="equals",
            area_id=area("et.hopr_constituency", code(form)),
            kind="standard",
        )
        p.name(unit, form["constituency_en"], "en", "official")
        url = form_url(poll, region, "hopr")
        page = key("page", poll, region, form["page"])
        p.add(
            "source_page",
            id=page,
            artifact_id=_artifact_id(url),
            page_number=form["page"],
            image_key=f"pages/{SOURCE}/{raw.cached(SOURCE, url).sha256}-{form['page']}.png",
            width=form["size"][0],
            height=form["size"][1],
        )
        for index, measure in LINES.items():
            value = form["lines"][index]
            _put(p, result_set, unit, None, measure, value)
            if value is not None:
                _locate(p, result_set, unit, None, measure, page, form["line_boxes"][index])
        winner = form["winner"]
        lang = REGIONS[region][2]
        for candidate in form["candidates"]:
            n = candidate["row"]
            option = key("option", tag, n)
            code_ = party_key(candidate["party"])
            independent = code_ == "independent"
            name = candidate["name"]
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=None if independent or code_ is None else party(code_),
                ballot_label=name,
            )
            person = key("person", tag, n)
            english = form.get("winner_en") if n == winner else None
            p.add("person", id=person, gender=english["gender"] if english else None)
            p.name(person, name, lang if _latin(name) else "am", "official")
            if english:
                p.name(person, english["name"], "en", "english")
            p.add(
                "candidacy",
                id=key("candidacy", tag, n),
                person_id=person,
                option_id=option,
                role="head",
                ballot_name=name,
                member_of_party_id=None if independent or code_ is None else party(code_),
                is_independent=independent,
            )
            p.add("outcome", result_set_id=result_set, option_id=option, kind="elected" if n == winner else "lost")
            _put(p, result_set, unit, option, VOTES, candidate["votes"])
            if candidate["votes"] is not None:
                _locate(p, result_set, unit, option, VOTES, page, candidate["box"])
    return p
