import html
import os
import re
import ssl
import unicodedata
from dataclasses import dataclass
from datetime import date
from uuid import UUID

import certifi
import polars as pl

from ea import raw
from ea.changeset import slug
from ea.package import Draft
from ea.sources.it.interno import BLANK, INVALID, REGISTERED, VOTERS, VOTES
from ea.sources.it.storico import EXISTING, NEW, cased
from ea.store import DATA, connect

SOURCE = "it.speciali"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
VDA = "https://www.regione.vda.it/amministrazione/Elezioni/Dati_e_risultati/elezioni/VotiLista_i.aspx?idele={}&ord=1&setcar=n"
SICILY = "https://www.elezioni.regione.sicilia.it/regionali{}/"
# The Sicilian results host serves its Let's Encrypt certificate without the YR1 intermediate that signed it; this is
# the certificate's own issuer URL.
INTERMEDIATE = "http://yr1.i.lencr.org/"


@dataclass(frozen=True)
class Held:
    last: date
    reading: int
    size: int
    winner: str


# Assemblea regionale siciliana elections on the Region's results site: the last day of voting, the number of the
# turnout reading at the close of the polls, the Assembly's size, and the surname of the President elected.
SICILY_HELD = {
    date(2008, 4, 13): Held(date(2008, 4, 14), 4, 90, "LOMBARDO"),
    date(2012, 10, 28): Held(date(2012, 10, 28), 3, 90, "CROCETTA"),
    date(2017, 11, 5): Held(date(2017, 11, 5), 3, 70, "MUSUMECI"),
    date(2022, 9, 25): Held(date(2022, 9, 25), 3, 70, "SCHIFANI"),
}
# Provinces whose summary page, and so the regional summary, prints other totals than the province's municipal pages
# add up to, for the candidates and the lists alike, mostly fewer. The municipal pages are loaded: in Siracusa they
# put valid votes for President at 95 percent of voters, as in every other province, while the summary puts them at
# 85 percent.
STALE = {date(2022, 9, 25): {"AG", "CL", "SR", "TP"}}
# Voters the municipal turnout rows of a province add up to beyond the regional turnout page's row for it.
TURNOUT_GAPS = {(date(2022, 9, 25), "CT"): 1}
# The nine provinces, which are the Assembly's constituencies: the site's folder and the ISTAT province code.
PROVINCES = {
    "AG": ("Agrigento", "084"),
    "CL": ("Caltanissetta", "085"),
    "CT": ("Catania", "087"),
    "EN": ("Enna", "086"),
    "ME": ("Messina", "083"),
    "PA": ("Palermo", "082"),
    "RG": ("Ragusa", "088"),
    "SR": ("Siracusa", "089"),
    "TP": ("Trapani", "081"),
}
# Municipalities the site names otherwise than ISTAT does today.
SICILY_ALIASES = {
    ("AG", "Sant'Elisabetta"): "Santa Elisabetta",
    ("CL", "Santa Caterina Villermosa"): "Santa Caterina Villarmosa",
    ("CT", "Castel di Judica"): "Castel di Iudica",
    ("PA", "Campofelice Roccella"): "Campofelice di Roccella",
}
# Sicilian provincial lists that are a national party's own list under a longer label.
SICILY_PARTIES = {
    "PARTITO DEMOCRATICO - FINOCCHIARO PRESIDENTE": "PARTITO DEMOCRATICO",
    "IL POPOLO DELLA LIBERTA' - BERLUSCONI PRESIDENTE": "IL POPOLO DELLA LIBERTA'",
    "ITALIA DEI VALORI LISTA DI PIETRO": "DI PIETRO ITALIA DEI VALORI",
    "UNIONE DEI DEMOCRATICI CRISTIANI E DEI DEMOCRATICI DI CENTRO": "UNIONE DI CENTRO",
    "MOVIMENTO 5 STELLE 2050": "MOVIMENTO 5 STELLE",
    "PARTITO DEMOCRATICO CROCETTA PRESIDENTE": "PARTITO DEMOCRATICO",
    "IL POPOLO DELLA LIBERTA' MUSUMECI PRESIDENTE": "IL POPOLO DELLA LIBERTA'",
    "UNIONE DEI DEMOCRATICI CRISTIANI E DEI DEMOCRATICI DI CENTRO (U. D. C. UNIONE DI CENTRO)": "UNIONE DI CENTRO",
    "PD - PARTITO DEMOCRATICO - MICARI PRESIDENTE": "PARTITO DEMOCRATICO",
    "MUSUMECI - FORZA ITALIA - BERLUSCONI": "FORZA ITALIA",
}
# Forza Italia was refounded in 2013, so its later lists are the new party, not the one of 1994 to 2009.
SICILY_KEYS = {
    ("FORZA ITALIA", 2017): ("it.party", "forza-italia"),
    ("FORZA ITALIA", 2022): ("it.party", "forza-italia"),
}


@dataclass(frozen=True)
class Region:
    iso: str
    slug: str
    native: str
    english: str
    council_it: str
    council_en: str
    publisher: str
    title: str
    home: str
    counted: str
    allocated: str


REGIONS = {
    "02": Region(
        iso="IT-23",
        slug="valle-d-aosta",
        native="Valle d'Aosta",
        english="Aosta Valley",
        council_it="Consiglio regionale della Valle d'Aosta",
        council_en="Regional Council of the Aosta Valley",
        publisher="Regione autonoma Valle d'Aosta",
        title="Elezioni: dati e risultati, elezioni regionali",
        home="https://www.regione.vda.it/amministrazione/Elezioni/Dati_e_risultati/Risultati/Elezioni_regionali/default_i.aspx",
        counted=(
            "Results are the Aosta Valley Region's regional summary of list votes and seats, from its election "
            "results pages."
        ),
        allocated=(
            "The council's 35 seats are allocated by list across the whole region, with a majority bonus for a list "
            "or group of lists with a common program, and the council elects the President of the Region."
        ),
    ),
    "19": Region(
        iso="IT-82",
        slug="sicilia",
        native="Sicilia",
        english="Sicily",
        council_it="Assemblea regionale siciliana",
        council_en="Sicilian Regional Assembly",
        publisher="Regione Siciliana",
        title="Elezioni regionali: risultati provvisori degli scrutini",
        home="https://www.elezioni.regione.sicilia.it/",
        counted=(
            "Results are the provisional count by municipality that the municipalities reported through the "
            "prefectures, from the Sicilian Region's election results site (Assessorato delle autonomie locali, "
            "Servizio elettorale); the official results are those the provincial central offices and the regional "
            "central office proclaim. Turnout is the reading at the close of the polls; the site does not give blank "
            "or invalid ballots."
        ),
        allocated=(
            "These are the votes for the provincial lists, which share out most of the Assembly's {size} seats in "
            "the nine provincial constituencies; the others go to the President, the runner-up, and the regional "
            "list of the winning coalition. Seats are not shown, because the site allocates them from the "
            "provisional count."
        ),
    ),
}
SICILY_PRESIDENT = (
    "The President of the Region has been elected directly since 2001, by plurality, on a separate vote for a "
    "regional list headed by each candidate."
)
VDA_HELD = {
    (date(2003, 6, 8), date(2003, 6, 8)): 62,
    (date(2008, 5, 25), date(2008, 5, 25)): 92,
    (date(2013, 5, 26), date(2013, 5, 26)): 113,
    (date(2018, 5, 20), date(2018, 5, 20)): 137,
    (date(2020, 9, 20), date(2020, 9, 21)): 142,
    (date(2025, 9, 28), date(2025, 9, 28)): 190,
}
# The region's results pages start in 2003 and its printed volumes for earlier councils are not online, so
# 1949-1998 comes from two sources that agree on every total: the Istituto Cattaneo's table of special-statute
# regional elections (ISTAT's 1990 compendium through 1988, the Interior Ministry for 1993 and 1998) for turnout,
# and the Italian Wikipedia's article on each election for votes and seats by list.
VDA_HISTORY = [
    date(1949, 4, 24),
    date(1954, 11, 14),
    date(1959, 5, 17),
    date(1963, 10, 27),
    date(1968, 4, 21),
    date(1973, 6, 10),
    date(1978, 6, 25),
    date(1983, 6, 26),
    date(1988, 6, 26),
    date(1993, 5, 30),
    date(1998, 5, 31),
]
CATTANEO = "https://web.archive.org/web/20110722091126id_/http://www.cattaneo.org/archivi/adele/regioni/speciale.xls"
CATTANEO_SHEETS = ["Valle d'Aosta 1949-1988", "Valle d'Aosta 1993-2003"]
CATTANEO_LABELS = {
    "Totale voti": "listed",
    "Elettori": "registered",
    "Votanti": "voters",
    "Voti non validi": "invalid",
    "di cui schede bianche": "blank",
}
WIKIPEDIA = "https://it.wikipedia.org/w/index.php?title=Elezioni_regionali_in_Valle_d%27Aosta_del_{}&action=raw"
HISTORY_SOURCES = {
    "totals": (
        "Istituto Carlo Cattaneo",
        "Elezioni regionali nelle regioni a statuto speciale",
        "academic",
        "http://www.cattaneo.org/archivi/adele/regioni/speciale.xls",
        None,
    ),
    "lists": (
        "Wikipedia",
        "Elezioni regionali in Valle d'Aosta",
        "community",
        "https://it.wikipedia.org/wiki/Elezioni_regionali_in_Valle_d%27Aosta",
        "CC BY-SA 4.0",
    ),
}
PANACHAGE = date(1959, 12, 31)
# Lists that stand for one party, by the Wikipedia article they link to (or their name when the link is piped to
# another name), mapped to the labels `party_of` resolves; any other list is its own list vehicle.
HISTORY_PARTIES = {
    "Democrazia Cristiana": "DC",
    "Partito Comunista Italiano": "PCI",
    "Partito Socialista Italiano": "PSI",
    "Partito Socialista Democratico Italiano": "PSDI",
    "Partito Liberale Italiano": "PLI",
    "Partito Repubblicano Italiano": "PRI",
    "Movimento Sociale Italiano": "MSI",
    "Movimento Sociale Italiano - Destra Nazionale": "MSI-DN",
    "PSI-PSDI Unificati": "PSU",
    "Partito Socialista Italiano di Unità Proletaria (1964)": "PSIUP",
    "Democrazia Proletaria": "DEM.PROL",
    "Democrazia Nazionale - Costituente di Destra": "DN-CD",
    "Union Valdôtaine": "UV",
    "Lega Nord": "LEGA NORD",
    "Lega Nord - Val d'Aohta Libra": "LEGA NORD",
    "Forza Italia (1994)": "FORZA ITALIA",
    "Partito della Rifondazione Comunista": "RIFONDAZIONE COMUNISTA",
    "Alleanza Nazionale": "ALLEANZA NAZIONALE",
    "Union Valdôtaine Progressiste (1972)": "UVP-1972",
    "Democratici Popolari": "DEMOCRATICI POPOLARI",
    "Rassemblement Valdôtain (1963)": "RASSEMBLEMENT VALDÔTAIN",
    "Rassemblement Indépendant Valdôtain": "RASSEMBLEMENT VALDÔTAIN",
    "Autonomistes Démocrates Progressistes": "AUTONOMISTES DÉMOCRATES PROGRESSISTES",
}
# Regional parties of the earlier councils, keyed apart from later parties that took the same name.
FORMER_PARTIES = {
    "UVP-1972": ("union-valdotaine-progressiste-1972", "Union Valdôtaine Progressiste", "UVP"),
    "DEMOCRATICI POPOLARI": ("democratici-popolari", "Democratici Popolari", None),
    "RASSEMBLEMENT VALDÔTAIN": ("rassemblement-valdotain", "Rassemblement Valdôtain", "RV"),
    "AUTONOMISTES DÉMOCRATES PROGRESSISTES": (
        "autonomistes-democrates-progressistes",
        "Autonomistes Démocrates Progressistes",
        "ADP",
    ),
}
PARTIES = {
    "PARTITO DEMOCRATICO": "PARTITO DEMOCRATICO",
    "MOVIMENTO 5 STELLE": "MOVIMENTO 5 STELLE",
    "MOVIMENTO CINQUE STELLE": "MOVIMENTO 5 STELLE",
    "IL POPOLO DELLA LIBERTÀ": "IL POPOLO DELLA LIBERTA'",
    "POPOLO DELLA LIBERTA'": "IL POPOLO DELLA LIBERTA'",
    "LEGA SALVINI VALLÉE D'AOSTE": "LEGA",
    "LEGA VALLÉE D'AOSTE": "LEGA",
    "FRATELLI D'ITALIA - GIORGIA MELONI": "FRATELLI D'ITALIA",
}
REGIONAL_PARTIES = {
    "UNION VALDÔTAINE": ("Union Valdôtaine", "UV"),
    "STELLA ALPINA": ("Stella Alpina", "SA"),
    "UNION VALDÔTAINE PROGRESSISTE": ("Union Valdôtaine Progressiste", "UVP"),
    "FÉDÉRATION AUTONOMISTE": ("Fédération Autonomiste", "FA"),
    "FEDERAZIONE AUTONOMISTA - FEDERATION AUTONOMISTE": ("Fédération Autonomiste", "FA"),
    "ALPE": ("Autonomie Liberté Participation Écologie", "ALPE"),
}
NOTES = {
    ("02", date(2025, 9, 28)): (
        "Seats are as the region shows them after the Council of State's ruling of May 7, 2026, which upheld "
        "Cristina Machet's removal from the council; her seat went to Andrea Campotaro of Alleanza Verdi Sinistra, "
        "the list with the lower vote total among those tied on remainders."
    ),
}
HISTORY_COUNTED = (
    "Turnout (registered voters, voters, and blank and invalid ballots) is from the Istituto Cattaneo's table of "
    "special-statute regional elections, which cites ISTAT's 45 anni di elezioni in Italia 1946-1990 through 1988 and "
    "the Interior Ministry for 1993 and 1998. Votes and seats by list are from the Italian Wikipedia's article on the "
    "election, and their total matches the table's."
)
HISTORY_ALLOCATED = {
    True: (
        "The council's 35 seats were filled by a majoritarian limited vote with panachage, in which voters could choose "
        "candidates from more than one list and the leading list took most of the seats. Each list's votes are its "
        "theoretical head votes, the average vote of its candidates, so they do not add up to the valid ballots."
    ),
    False: (
        "The council's 35 seats were allocated by proportional representation among lists across the whole region, "
        "and the council elected the President of the Region."
    ),
}
COUNT_LABELS = {"Elettori": "registered", "Votanti": "voters", "Schede bianche": "blank", "Schede nulle": "null"}


def vda_url(number: int) -> str:
    return VDA.format(number)


def sicily_url(day: date, path: str) -> str:
    return SICILY.format(day.year) + path


def sicily_pages(day: date) -> list[str]:
    """The regional and provincial report pages of one election, before the municipal pages they link to."""
    reading = SICILY_HELD[day].reading
    pages = [
        "rep_7/riepilogoRegionale.html",
        "rep_3/votiListeRegionali.html",
        f"rep_6/affluenzaRegionale{reading}.html",
    ]
    for code, (folder, _) in PROVINCES.items():
        pages += [
            f"rep_7/{folder}/riepilogoProvinciale{code}.html",
            f"rep_6/{folder}/affluenzaProvinciale{code}{reading}.html",
            f"rep_13/{folder}/votiComunaliListeProvinciali{code}.html",
            f"rep_3/{folder}/votiListeRegionaliProvincia{code}.html",
        ]
    return [sicily_url(day, page) for page in pages]


def page_text(url: str) -> str:
    content = raw.cached(SOURCE, url).path.read_bytes()
    charset = re.search(rb"charset=([\w-]+)", content)
    return content.decode(charset[1].decode() if charset else "iso-8859-15", errors="strict")


def sicily_communes(day: date, code: str) -> list[tuple[str, str]]:
    """The municipal pages of presidential votes a province's page links to, with each municipality's name."""
    folder = PROVINCES[code][0]
    page = page_text(sicily_url(day, f"rep_3/{folder}/votiListeRegionaliProvincia{code}.html"))
    return [
        (sicily_url(day, f"rep_3/{folder}/{target}"), html.unescape(name).strip())
        for target, name in re.findall(r'<option value="(votiListeRegionaliComune\d+\.html)">([^<]+)</option>', page)
    ]


def context() -> ssl.SSLContext:
    """The platform's trusted roots plus the intermediate the Sicilian results host leaves out of its chain."""
    artifact = raw.fetch(SOURCE, INTERMEDIATE, suffix=".crt")
    trusted = ssl.create_default_context(cafile=os.environ.get("SSL_CERT_FILE") or certifi.where())
    trusted.load_verify_locations(cadata=ssl.DER_cert_to_PEM_cert(artifact.path.read_bytes()))
    return trusted


def fetch() -> None:
    for number in VDA_HELD.values():
        raw.fetch(SOURCE, vda_url(number), suffix=".html")
    raw.fetch(SOURCE, CATTANEO, suffix=".xls")
    for day in VDA_HISTORY:
        raw.fetch(SOURCE, WIKIPEDIA.format(day.year), suffix=".wiki")
    trusted = context()
    for day in SICILY_HELD:
        for url in sicily_pages(day):
            raw.fetch(SOURCE, url, suffix=".html", verify=trusted)
        for code in PROVINCES:
            for url, _ in sicily_communes(day, code):
                raw.fetch(SOURCE, url, suffix=".html", verify=trusted)


def number(text: str) -> int:
    return int(text.replace(".", "").strip())


def cattaneo(path: str) -> dict[date, dict[str, int]]:
    """Turnout by election day from the Cattaneo table's absolute-values block for the Aosta Valley."""
    totals: dict[date, dict[str, int]] = {}
    for sheet in CATTANEO_SHEETS:
        frame = pl.read_excel(path, sheet_name=sheet, has_header=False, infer_schema_length=0)
        rows = [[None if cell is None else str(cell).strip() for cell in row] for row in frame.iter_rows()]
        header = next(row for row in rows if row[0] == "Liste")
        days: dict[int, date] = {}
        for column, cell in enumerate(header[1:], start=1):
            if cell and (found := re.match(r"(\d{2})/(\d{2})/(\d{4})|(\d{4})-(\d{2})-(\d{2})", cell)):
                day, month, year = (found[1], found[2], found[3]) if found[1] else (found[6], found[5], found[4])
                days[column] = date(int(year), int(month), int(day))
        seen: set[str] = set()
        for row in rows[rows.index(header) :]:
            key = CATTANEO_LABELS.get(row[0] or "")
            if key is None or key in seen:
                continue
            seen.add(key)
            for column, day in days.items():
                totals.setdefault(day, {})[key] = int(float(str(row[column])))
    return totals


def wikipedia(text: str) -> list[dict[str, str | int | None]]:
    """Lists, votes, and seats from an article's {{Elezione}} results template."""
    block = re.search(r"(?s)\{\{Elezione\s*\n(.*?)\n\}\}", text)
    if block is None:
        raise ValueError("no {{Elezione}} template")
    cells = [re.sub(r"^\|\s*", "", cell.strip()) for cell in block[1].split("\n|")]
    cells = [cell for cell in cells if cell and not re.match(r"(votanti|elettori)\s*=", cell)]
    if len(cells) % 3:
        raise ValueError("the results template is not in label, votes, seats triples")
    lists = []
    for shown, votes, seats in zip(cells[::3], cells[1::3], cells[2::3], strict=True):
        shown = re.sub(r"(?s)<ref[^>]*/>|<ref.*?</ref>|<small>.*?</small>", "", shown)
        shown = re.sub(r"<br\s*/?>|\s\([A-Z][A-Z-]+\)", " ", shown).strip()
        links = re.findall(r"\[\[([^|\]]+)(?:\|([^\]]+))?\]\]", shown)
        label = re.sub(r"\s+", " ", re.sub(r"\[\[(?:[^|\]]+\|)?([^\]]+)\]\]", r"\1", shown)).strip()
        label = label[1:-1] if re.fullmatch(r'"[^"]+"', label) else label
        bare = len(links) == 1 and re.fullmatch(r"\[\[[^\]]+\]\]", shown)
        target = links[0][0] if bare and re.sub(r" \([^)]*\)$", "", links[0][0]) == label else label
        lists.append(
            {
                "list": label,
                "group": None,
                "votes": number(votes),
                "seats": 0 if seats == "-" else int(seats),
                "party": HISTORY_PARTIES.get(target),
            }
        )
    return lists


def vda(page: str) -> tuple[dict[str, int], list[dict[str, object]]]:
    text = re.sub(r"(?s)<script.*?</script>|<style.*?</style>", "", page)
    text = re.sub(r"</t[dh]>", "|", text)
    text = html.unescape(re.sub(r"<[^>]+>", " ", text))
    text = re.sub(r"\s+", " ", text)
    start = text.index("Riepilogo regionale")
    end = text.find("Riepilogo per programma", start)
    end = text.index("Elenco ", start) if end < 0 else end
    summary = text[start:end]
    counts = {
        key: number(match[1])
        for label, key in COUNT_LABELS.items()
        if (match := re.search(rf"{label}\s*\|\s*([\d.]+)", summary))
    }
    body = summary[summary.index("Seggi|") + len("Seggi|") :]
    lists = []
    for match in re.finditer(
        r"\|\s*\|\s*([^|]+?)\s*\|\s*(?:(\d*)\s*\|)?\s*([\d.]+)\s*\|\s*[\d,]+%\s*\|\s*\d+\s*\|\s*(\d+|-)\s*\|", body
    ):
        lists.append(
            {
                "list": match[1].strip(),
                "group": int(match[2]) if match[2] else None,
                "votes": number(match[3]),
                "seats": 0 if match[4] == "-" else int(match[4]),
            }
        )
    return counts, lists


def fold(text: str) -> str:
    """A municipality's name for matching: no accents, case, spaces, or punctuation."""
    plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]", "", plain.lower())


def proper(text: str) -> str:
    """A name the site prints in capitals, in ordinary case, with the final accent it writes as an apostrophe
    (MICCICHE'); words already in mixed case keep their case."""
    words = (
        re.sub(r"[^\W\d_]+", lambda m: m[0][0].upper() + m[0][1:].lower(), word) if word.isupper() else word
        for word in text.split()
    )
    return " ".join(re.sub(r"([aeiou])'$", lambda m: "àèìòù"["aeiou".index(m[1])], word) for word in words)


def tokens(page: str) -> list[str]:
    """The non-empty cell texts of a Sicilian report page, in order, up to the footer."""
    text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<option.*?</option>|<!--.*?-->", "", page)
    text = html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"(?i)</t[dh]>|</tr>", "|", text)))
    cells = [re.sub(r"\s+", " ", cell).strip() for cell in text.split("|")]
    cells = [cell for cell in cells if cell]
    start = next(i for i, cell in enumerate(cells) if cell.startswith("ELEZIONI REGIONALI DEL"))
    end = next(i for i, cell in enumerate(cells) if cell.startswith("Trattasi di dati provvisori"))
    return cells[start:end]


def whole(text: str) -> int:
    if not re.fullmatch(r"\d{1,3}(\.\d{3})*", text):
        raise ValueError(f"not a count: {text!r}")
    return int(text.replace(".", ""))


def counted(text: str) -> bool:
    return re.fullmatch(r"\d{1,3}(\.\d{3})*", text) is not None


def candidate(text: str) -> str:
    """A candidate's name as printed, without the nickname the site adds after it."""
    return re.sub(r"\s+Dett[oa](/[oa])?\s+.*$", "", text).strip()


@dataclass(frozen=True)
class Coalition:
    number: int
    label: str
    candidate: str
    votes: int
    lists: list[tuple[str, int]]


def coalitions(cells: list[str]) -> list[Coalition]:
    """The regional summary's regional lists, each with its candidate for President and the provincial lists linked
    to it, with their regional totals."""
    found: list[Coalition] = []
    for at, cell in enumerate(cells):
        match = re.search(r"\bLISTA REGIONALE N\. (\d+)$", cell)
        if match is None:
            continue
        block = cells[at + 1 : next((i for i in range(at + 1, len(cells)) if "LISTA REGIONALE N. " in cells[i]), None)]
        head = block.index("Candidato presidente")
        if block[head + 1 : head + 3] != ["Voti", "%"]:
            raise ValueError(f"unexpected regional list block {block[:8]}")
        lists: list[tuple[str, int]] = []
        # A provincial page shows a regional list with no linked list standing in the province without the table.
        if "Seggi (*)" in block:
            rows = block[block.index("Seggi (*)") + 1 : block.index("Totali")]
            lists = [(rows[i], whole(rows[i + 1])) for i in range(0, len(rows), 4)]
            if any(not counted(rows[i + 1]) or not rows[i + 3].isdigit() for i in range(0, len(rows), 4)):
                raise ValueError(f"unexpected provincial lists {rows}")
            total = block[block.index("Totali") + 1]
            if sum(votes for _, votes in lists) != whole(total):
                raise ValueError(f"regional list {match[1]}: provincial lists do not add up to {total}")
        found.append(Coalition(int(match[1]), block[0], candidate(block[head + 3]), whole(block[head + 4]), lists))
    return found


def presidential(cells: list[str]) -> tuple[list[tuple[int, str, str, int]], int]:
    """The candidates for President on a regional, provincial, or municipal page: number, name, regional list, and
    votes, with the printed total."""
    start = cells.index("Voti validi") + 2
    end = cells.index("TOTALE")
    rows = cells[start:end]
    if len(rows) % 5:
        raise ValueError(f"unexpected candidate rows {rows}")
    found = []
    for i in range(0, len(rows), 5):
        number, name, label, votes, share = rows[i : i + 5]
        if not number.isdigit() or not share.endswith("%"):
            raise ValueError(f"unexpected candidate row {rows[i : i + 5]}")
        found.append((int(number), candidate(name), label, whole(votes)))
    return found, whole(cells[end + 1])


def municipal_lists(cells: list[str]) -> list[tuple[str, str, int | None]]:
    """The province's list votes by municipality: (municipality, list, votes), with None where the list did not
    stand."""
    at = next(i for i, cell in enumerate(cells) if "Provincia di " in cell)
    rest = cells[at + 1 :]
    first = next(i for i, cell in enumerate(rest) if counted(cell) or cell == "-")
    labels = rest[: first - 1]
    width = len(labels) + 1
    body = rest[first - 1 :]
    if len(body) % width:
        raise ValueError(f"{cells[at]}: rows of {width} cells do not fill the table")
    found = []
    for i in range(0, len(body), width):
        place, *values = body[i : i + width]
        if counted(place) or not all(counted(value) or value == "-" for value in values):
            raise ValueError(f"{cells[at]}: unexpected row {body[i : i + width]}")
        found += [(place, label, None if value == "-" else whole(value)) for label, value in zip(labels, values)]
    return found


def turnout(cells: list[str]) -> list[tuple[str, int, int]]:
    """Registered voters and voters by place at a turnout reading: the first pair of each row."""
    start = cells.index("%", cells.index("Votanti")) + 1
    start = cells.index("%", start) + 1
    rows = cells[start:]
    found = []
    i = 0
    while i < len(rows) and not rows[i].startswith("Torna"):
        place, registered, voters, share = rows[i : i + 4]
        if counted(place) or not share.endswith("%"):
            raise ValueError(f"unexpected turnout row {rows[i : i + 7]}")
        found.append((place, whole(registered), whole(voters)))
        # A place that did not vote at the earlier election the page compares with has no second triple.
        i += 7 if i + 4 < len(rows) and counted(rows[i + 4]) else 4
    return found


def sicily() -> tuple[pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame, pl.DataFrame]:
    """Parse the Region's provisional count of each Assembly election by municipality, checking each table against
    the regional and provincial totals the site prints."""
    held: list[dict[str, object]] = []
    linked: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    lists: list[dict[str, object]] = []
    voting: list[dict[str, object]] = []
    for day, info in SICILY_HELD.items():
        summary = coalitions(tokens(page_text(sicily_url(day, "rep_7/riepilogoRegionale.html"))))
        regional, total = presidential(tokens(page_text(sicily_url(day, "rep_3/votiListeRegionali.html"))))
        if sum(votes for *_, votes in regional) != total:
            raise ValueError(f"{day}: candidates do not add up to {total}")
        if {(n, name, votes) for n, name, _, votes in regional} != {(c.number, c.candidate, c.votes) for c in summary}:
            raise ValueError(f"{day}: the regional summary and the candidates' votes differ")
        coalition = {label: c.number for c in summary for label, _ in c.lists}
        list_totals = {label: votes for c in summary for label, votes in c.lists}
        # The municipal tables of some years print the summary's list names in another case and spacing.
        named = {fold(label): label for label in coalition}
        if len(named) != len(coalition):
            raise ValueError(f"{day}: two provincial lists fold to one name")
        for c in summary:
            linked += [{"day": day, "number": c.number, "label": c.label, "list": label} for label, _ in c.lists]
        found_lists: dict[str, int] = {}
        found_candidates: dict[int, int] = {}
        provincial_lists: dict[str, int] = {}
        provincial_candidates: dict[int, int] = {}
        stale: list[str] = []
        registered = voters = 0
        for code, (folder, _) in PROVINCES.items():
            province = coalitions(tokens(page_text(sicily_url(day, f"rep_7/{folder}/riepilogoProvinciale{code}.html"))))
            summary_lists = {label: votes for c in province for label, votes in c.lists}
            summary_candidates = {c.number: c.votes for c in province}
            for label, votes in summary_lists.items():
                provincial_lists[label] = provincial_lists.get(label, 0) + votes
            for number, votes in summary_candidates.items():
                provincial_candidates[number] = provincial_candidates.get(number, 0) + votes
            province_lists: dict[str, int] = {}
            province_candidates: dict[int, int] = {}
            url = sicily_url(day, f"rep_13/{folder}/votiComunaliListeProvinciali{code}.html")
            for place, printed_label, votes in municipal_lists(tokens(page_text(url))):
                label = named.get(fold(printed_label))
                if label is None:
                    raise ValueError(f"{day} {code}: {printed_label} is not in the regional summary")
                if votes is not None:
                    province_lists[label] = province_lists.get(label, 0) + votes
                    lists.append(
                        {"day": day, "province": code, "place": place, "list": label, "votes": votes, "url": url}
                    )
            url = sicily_url(day, f"rep_6/{folder}/affluenzaProvinciale{code}{info.reading}.html")
            for place, enrolled, voted in turnout(tokens(page_text(url))):
                registered += enrolled
                voters += voted
                voting.append(
                    {"day": day, "province": code, "place": place, "registered": enrolled, "voters": voted, "url": url}
                )
            places = sicily_communes(day, code)
            if len(places) != len({place for place, _ in places}):
                raise ValueError(f"{day} {code}: a municipal page repeats")
            for url, place in places:
                rows, printed = presidential(tokens(page_text(url)))
                if sum(votes for *_, votes in rows) != printed:
                    raise ValueError(f"{day} {place}: candidates do not add up to {printed}")
                for number, name, label, votes in rows:
                    province_candidates[number] = province_candidates.get(number, 0) + votes
                    candidates.append(
                        {
                            "day": day,
                            "province": code,
                            "place": place,
                            "number": number,
                            "candidate": name,
                            "label": label,
                            "votes": votes,
                            "url": url,
                        }
                    )
            summary_candidates = {number: votes for number, votes in summary_candidates.items() if votes}
            province_candidates = {number: votes for number, votes in province_candidates.items() if votes}
            if province_lists != summary_lists or province_candidates != summary_candidates:
                if code not in STALE.get(day, ()):
                    raise ValueError(f"{day} {code}: the municipal pages do not add up to the provincial summary")
                stale.append(folder)
            elif code in STALE.get(day, ()):
                raise ValueError(f"{day} {code}: the provincial summary now agrees with its municipalities")
            for label, votes in province_lists.items():
                found_lists[label] = found_lists.get(label, 0) + votes
            for number, votes in province_candidates.items():
                found_candidates[number] = found_candidates.get(number, 0) + votes
        if provincial_lists != list_totals or provincial_candidates != {n: v for n, _, _, v in regional if v}:
            raise ValueError(f"{day}: the provincial summaries do not add up to the regional summary")
        regional_turnout = turnout(tokens(page_text(sicily_url(day, f"rep_6/affluenzaRegionale{info.reading}.html"))))
        by_province = {place: (enrolled, voted) for place, enrolled, voted in regional_turnout}
        for code, (folder, _) in PROVINCES.items():
            rows = [row for row in voting if row["day"] == day and row["province"] == code]
            enrolled = sum(int(str(row["registered"])) for row in rows)
            voted = sum(int(str(row["voters"])) for row in rows) - TURNOUT_GAPS.get((day, code), 0)
            if (enrolled, voted) != by_province[folder]:
                raise ValueError(f"{day} {code}: turnout by municipality does not add up to {by_province[folder]}")
        if by_province["Totale"] != (registered, voters - sum(n for (d, _), n in TURNOUT_GAPS.items() if d == day)):
            raise ValueError(f"{day}: turnout by province does not add up to {by_province['Totale']}")
        held.append(
            {
                "day": day,
                "last": info.last,
                "size": info.size,
                "winner": info.winner,
                "summary_president": total,
                "counted_president": sum(found_candidates.values()),
                "summary_lists": sum(list_totals.values()),
                "counted_lists": sum(found_lists.values()),
                "stale": ", ".join(stale),
            }
        )
    return (
        pl.DataFrame(held),
        pl.DataFrame(linked),
        pl.DataFrame(candidates),
        pl.DataFrame(lists),
        pl.DataFrame(voting),
    )


def parse() -> None:
    elections = []
    lists = []
    for (day, last), number_ in VDA_HELD.items():
        counts, rows = vda(raw.cached(SOURCE, vda_url(number_)).path.read_text(encoding="utf-8", errors="replace"))
        frame = pl.DataFrame(rows, schema_overrides={"group": pl.Int64})
        if frame["votes"].sum() != counts["voters"] - counts["blank"] - counts["null"]:
            raise ValueError(f"02 {day}: list votes do not add up to valid votes")
        if frame["seats"].sum() != 35:
            raise ValueError(f"02 {day}: seats do not add up to 35")
        url = vda_url(number_)
        elections.append(
            {"region": "02", "day": day, "last": last, "url": url, "counts_url": url, "size": 35, **counts}
        )
        lists.extend({"region": "02", "day": day, **row, "party": None} for row in rows)
    totals = cattaneo(str(raw.cached(SOURCE, CATTANEO).path))
    for day in VDA_HISTORY:
        url = WIKIPEDIA.format(day.year)
        rows = wikipedia(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
        total = totals[day]
        listed = sum(int(str(row["votes"])) for row in rows)
        valid = total["voters"] - total["invalid"]
        # Under the panachage of 1949-1959 list votes are theoretical head votes, which add up to the table's
        # total but not to the valid ballots; from 1963 they add up to the valid ballots. (The table's own 1963
        # total leaves out the Union Valdôtaine's 12,930 votes, which the valid ballots include.)
        if listed != (total["listed"] if day <= PANACHAGE else valid):
            raise ValueError(f"02 {day}: list votes {listed:,} do not add up")
        if sum(int(str(row["seats"])) for row in rows) != 35:
            raise ValueError(f"02 {day}: seats do not add up to 35")
        elections.append(
            {
                "region": "02",
                "day": day,
                "last": day,
                "url": url,
                "counts_url": CATTANEO,
                "size": 35,
                "registered": total["registered"],
                "voters": total["voters"],
                "blank": total["blank"],
                "null": total["invalid"] - total["blank"],
            }
        )
        lists.extend({"region": "02", "day": day, **row} for row in rows)
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(elections).write_parquet(PARSED / "elections.parquet")
    pl.DataFrame(lists, schema_overrides={"group": pl.Int64, "party": pl.String}).write_parquet(
        PARSED / "lists.parquet"
    )
    for name, frame in zip(("held", "linked", "candidates", "list_votes", "turnout"), sicily(), strict=True):
        frame.write_parquet(PARSED / f"sicily_{name}.parquet")


def emit() -> None:
    elections = pl.read_parquet(PARSED / "elections.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    with connect() as conn:
        cs = Draft(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Regional council elections in Italy's special-statute regions from the regions' own results, "
                "and from historical compilations before them, by region"
            ),
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        italy = cs.polity("IT")
        if italy in cs.minted:
            raise ValueError("publish it.interno before it.speciali")
        parties: dict[tuple[str, str], UUID] = {}

        def party_of(shown: str, spec: tuple[str, str] | None = None, party: str | None = None) -> UUID:
            label = party or PARTIES.get(shown.upper(), shown.upper())
            spec = spec or EXISTING.get(label)
            if spec is not None:
                if spec not in parties:
                    parties[spec] = cs.key(*spec)
                    if parties[spec] in cs.minted:
                        raise ValueError(f"{spec} is not in the database")
                return parties[spec]
            if label in FORMER_PARTIES:
                handle, full, abbreviation = FORMER_PARTIES[label]
                key, kind = ("it.party", handle), "party"
            elif label in REGIONAL_PARTIES:
                full, abbreviation = REGIONAL_PARTIES[label]
                key, kind = ("it.party", slug(full)), "party"
            elif label in NEW:
                full, abbreviation, kind = NEW[label]
                key = ("it.party", slug(full))
            else:
                full, abbreviation, kind = cased(shown) if shown.isupper() else shown, None, "list_vehicle"
                key = ("it.storico.list", slug(label))
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=italy)
                    if abbreviation is not None:
                        name(parties[key], abbreviation, "it", "abbreviation")
                    name(parties[key], full, "it")
            return parties[key]

        rows: list[dict[str, object]] = []

        def value(result_set: UUID, unit: UUID, option: UUID | None, measure: int, count: int | None) -> None:
            if count is not None:
                rows.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": None if option is None else str(option),
                        "measure": measure,
                        "value": count,
                    }
                )

        def result_set(key: str, election: UUID, contest: UUID | None, source: UUID, stage: str = "official") -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            return id

        for code in sorted(elections["region"].unique()):
            info = REGIONS[code]
            source = cs.source(info.publisher, info.title)
            if source in cs.minted:
                cs.add(
                    "source", id=source, kind="official_body", publisher=info.publisher, title=info.title, url=info.home
                )
            region = cs.polity(info.iso)
            area = cs.area("it.regione", code)
            unit = cs.unit("it.regione", code)
            if area in cs.minted or unit in cs.minted:
                raise ValueError("publish it.istat before it.speciali")
            if region in cs.minted or region in cs.own_entities:
                cs.add("polity", id=region, kind="region", iso_code=info.iso)
                cs.add("slug", path=f"it/{info.slug}", entity_id=region)
                cs.add("polity_parent", polity_id=region, parent_id=italy)
                cs.add("polity_territory", polity_id=region, area_id=area)
                name(region, info.native, "it")
                if info.english != info.native:
                    name(region, info.english, "en", "english")
            body = cs.key(f"{SOURCE}.body", code)
            if body in cs.minted:
                cs.add("body", id=body, polity_id=region, kind="unicameral")
                name(body, info.council_en)
                name(body, info.council_it, "it", "native")
            held = elections.filter(pl.col("region") == code).sort("day")
            days = held["day"].to_list()
            history: dict[str, UUID] = {}
            if (held["counts_url"] != held["url"]).any():
                for role, (publisher, title, kind, home, license) in HISTORY_SOURCES.items():
                    history[role] = cs.source(publisher, title)
                    if history[role] in cs.minted or history[role] in cs.own_entities:
                        cs.add(
                            "source",
                            id=history[role],
                            kind=kind,
                            publisher=publisher,
                            title=title,
                            url=home,
                            license=license,
                        )
            # The region's own elections go first, so a list vehicle that also ran before 2003 keeps their name for it.
            for total in held.sort(pl.col("counts_url") != pl.col("url"), "day").iter_rows(named=True):
                day = total["day"]
                earlier = total["counts_url"] != total["url"]
                totals_source = history["totals"] if earlier else source
                lists_source = history["lists"] if earlier else source
                cs.artifact(raw.cached(SOURCE, total["counts_url"]), totals_source)
                cs.artifact(raw.cached(SOURCE, total["url"]), lists_source)
                label = str(day.year) if sum(other.year == day.year for other in days) == 1 else f"{day:%B} {day.year}"
                election = cs.key(f"{SOURCE}.election", f"{code}:{day}")
                cs.add(
                    "election",
                    id=election,
                    polity_id=region,
                    kind="general",
                    date_from=day,
                    date_to=total["last"],
                    status="held",
                )
                name(election, f"{label} {info.english} regional election")
                name(election, f"Elezioni regionali in {info.native} del {day.year}", "it", "native")
                path = f"it/{info.slug}/{day}-general"
                cs.add("slug", path=path, entity_id=election)
                accounting = result_set(f"{code}:{day}:election", election, None, totals_source)
                value(accounting, unit, None, REGISTERED, total["registered"])
                value(accounting, unit, None, VOTERS, total["voters"])
                value(accounting, unit, None, BLANK, total["blank"])
                value(accounting, unit, None, INVALID, total["null"])
                contest = cs.key(f"{SOURCE}.contest", f"{code}:{day}:list")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="list_allocation",
                    body_id=body,
                    district_area_id=area,
                    magnitude=total["size"],
                    default_result_kind="counted",
                )
                name(contest, f"{label} {info.english} regional election, council lists")
                cs.add("slug", path=f"{path}/consiglio", entity_id=contest)
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="context",
                    body_md=" ".join(
                        [HISTORY_COUNTED, HISTORY_ALLOCATED[day <= PANACHAGE]]
                        if earlier
                        else [info.counted, info.allocated, *filter(None, [NOTES.get((code, day))])]
                    ),
                )
                list_set = result_set(f"{code}:{day}:list", election, contest, lists_source)
                for row in (
                    lists.filter(pl.col("region") == code, pl.col("day") == day)
                    .sort("votes", "list", descending=[True, False])
                    .iter_rows(named=True)
                ):
                    option = cs.key(f"{SOURCE}.option", f"{code}:{day}:list:{row['list']}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party_of(row["list"], party=row["party"]),
                        ballot_label=row["list"],
                    )
                    value(list_set, unit, option, VOTES, row["votes"])
                    if row["seats"]:
                        cs.add("outcome", result_set_id=list_set, option_id=option, kind="won", seats=row["seats"])

        held = pl.read_parquet(PARSED / "sicily_held.parquet")
        candidates = pl.read_parquet(PARSED / "sicily_candidates.parquet")
        list_votes = pl.read_parquet(PARSED / "sicily_list_votes.parquet")
        voting = pl.read_parquet(PARSED / "sicily_turnout.parquet")
        info = REGIONS["19"]
        source = cs.source(info.publisher, info.title)
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=info.publisher, title=info.title, url=info.home)
        region = cs.polity(info.iso)
        area = cs.area("it.regione", "19")
        body = cs.key("it.regionali.body", "19")
        office = cs.key("it.regionali.office", "19")
        if {region, area, body, office} & cs.minted:
            raise ValueError("publish it.istat and it.regionali before it.speciali")
        istat: dict[tuple[str, str], set[str]] = {}
        for code, text in conn.execute(
            """select c.code, n.text from area a join area_code c on c.area_id = a.id
               join name n on n.entity_id = a.id where a.scheme_code = 'it.comune' and c.code ~ '^08[1-9]'"""
        ).fetchall():
            istat.setdefault((code[:3], fold(text)), set()).add(code)
        units: dict[tuple[str, str], UUID] = {}

        def municipality(province: str, place: str) -> UUID:
            if (province, place) not in units:
                found = istat.get((PROVINCES[province][1], fold(SICILY_ALIASES.get((province, place), place))), set())
                if len(found) != 1:
                    raise ValueError(f"{province} {place} matches municipalities {sorted(found)}")
                units[province, place] = cs.unit("it.comune", next(iter(found)))
                if units[province, place] in cs.minted:
                    raise ValueError(f"{province} {place} has no it.comune reporting unit")
            return units[province, place]

        for total in held.sort("day").iter_rows(named=True):
            day = total["day"]
            on = pl.col("day") == day
            for url in [*sicily_pages(day), *candidates.filter(on)["url"].unique().sort()]:
                cs.artifact(raw.cached(SOURCE, url), source)
            election = cs.key(f"{SOURCE}.election", f"19:{day}")
            cs.add(
                "election",
                id=election,
                polity_id=region,
                kind="general",
                date_from=day,
                date_to=total["last"],
                status="held",
            )
            name(election, f"{day.year} {info.english} regional election")
            name(election, f"Elezioni regionali in {info.native} del {day.year}", "it", "native")
            path = f"it/{info.slug}/{day}-general"
            cs.add("slug", path=path, entity_id=election)
            accounting = result_set(f"19:{day}:election", election, None, source, "preliminary")
            for row in voting.filter(on).iter_rows(named=True):
                unit = municipality(row["province"], row["place"])
                value(accounting, unit, None, REGISTERED, row["registered"])
                value(accounting, unit, None, VOTERS, row["voters"])

            president = cs.key(f"{SOURCE}.contest", f"19:{day}:president")
            cs.add(
                "contest",
                id=president,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                magnitude=1,
                default_result_kind="counted",
            )
            name(president, f"{day.year} {info.english} regional election, President")
            cs.add("slug", path=f"{path}/presidente", entity_id=president)
            differs = []
            if total["stale"]:
                differs.append(
                    f"The site's summary pages for {total['stale']} print other totals than their municipal pages add "
                    "up to; the figures here are the municipal pages', which add up to "
                    f"{total['counted_president']:,} votes for President and {total['counted_lists']:,} for the "
                    f"provincial lists, against {total['summary_president']:,} and {total['summary_lists']:,} in the "
                    "regional summary."
                )
            differs += [
                f"The municipal turnout rows of {PROVINCES[code][0]} add up to {gap:,} more voters than the regional "
                "turnout page."
                for (held_on, code), gap in TURNOUT_GAPS.items()
                if held_on == day
            ]
            cs.add(
                "note",
                entity_id=president,
                kind="context",
                body_md=" ".join([info.counted, SICILY_PRESIDENT, *differs]),
            )
            president_set = result_set(f"19:{day}:president", election, president, source, "preliminary")
            standing = (
                candidates.filter(on)
                .group_by("number", "candidate")
                .agg(pl.col("votes").sum())
                .sort("votes", "number", descending=[True, False])
            )
            for rank, row in enumerate(standing.iter_rows(named=True)):
                key = f"19:{day}:president:{row['number']}"
                option = cs.key(f"{SOURCE}.option", key)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=president,
                    kind="candidate",
                    ballot_position=row["number"],
                    ballot_label=row["candidate"],
                )
                person = cs.key(f"{SOURCE}.person", f"19:{day}:{row['number']}")
                if person in cs.minted:
                    cs.add("person", id=person)
                    name(person, proper(row["candidate"]), "it")
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", key),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["candidate"],
                )
                if rank == 0:
                    if row["candidate"].upper().split()[-1] != total["winner"]:
                        raise ValueError(f"{day}: {row['candidate']} leads, not {total['winner']}")
                    cs.add("outcome", result_set_id=president_set, option_id=option, kind="elected")
                for place in candidates.filter(on, pl.col("number") == row["number"]).iter_rows(named=True):
                    value(president_set, municipality(place["province"], place["place"]), option, VOTES, place["votes"])

            council = cs.key(f"{SOURCE}.contest", f"19:{day}:list")
            cs.add(
                "contest",
                id=council,
                election_id=election,
                kind="list_allocation",
                body_id=body,
                district_area_id=area,
                magnitude=total["size"],
                default_result_kind="counted",
            )
            name(council, f"{day.year} {info.english} regional election, council lists")
            cs.add("slug", path=f"{path}/consiglio", entity_id=council)
            cs.add(
                "note",
                entity_id=council,
                kind="context",
                body_md=" ".join([info.counted, info.allocated.format(size=total["size"]), *differs]),
            )
            council_set = result_set(f"19:{day}:list", election, council, source, "preliminary")
            for row in (
                list_votes.filter(on)
                .group_by("list")
                .agg(pl.col("votes").sum())
                .sort("votes", "list", descending=[True, False])
                .iter_rows(named=True)
            ):
                label = row["list"]
                option = cs.key(f"{SOURCE}.option", f"19:{day}:list:{label}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=council,
                    kind="list",
                    party_id=party_of(
                        (party := SICILY_PARTIES.get(label.upper(), label)), SICILY_KEYS.get((party.upper(), day.year))
                    ),
                    ballot_label=label,
                )
                for place in list_votes.filter(on, pl.col("list") == label).iter_rows(named=True):
                    value(council_set, municipality(place["province"], place["place"]), option, VOTES, place["votes"])
        cs.frame(
            "result_value",
            pl.DataFrame(rows, schema_overrides={"value": pl.Int64}).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.col("measure").cast(pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        cs.write()
