import difflib
import html
import io
import json
import re
import unicodedata
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.it.interno import key as interno_key
from ea.sources.it.interno import proper
from ea.sources.it.storico import EXISTING, NEW, NEW_OVERRIDES, NORTH, OVERRIDES, SOUTH, SYMBOLS
from ea.store import DATA, connect

SOURCE = "it.senato"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
ARCHIVE = "https://dait.interno.gov.it/documenti/opendata/senato/senato-{day:%Y%m%d}.zip"
ELIGENDO = (
    "https://elezionistorico.interno.gov.it/index.php?tpel=S&dtel={day:%d/%m/%Y}&tpa=I&tpe=R&lev0=0&levsut0=0"
    "&levsut1=1&es0=S&es1=S&ms=S&ne1={n}&lev1={n}"
)
NATIONAL = "https://elezionistorico.interno.gov.it/index.php?tpel=S&dtel={day:%d/%m/%Y}&tpa=I&tpe=A&lev0=0&levsut0=0&es0=S&ms=S"
QUERY = """PREFIX osr: <http://dati.senato.it/osr/>
PREFIX foaf: <http://xmlns.com/foaf/0.1/>
SELECT DISTINCT ?s ?m ?cognome ?nome ?nascita ?genere ?leg ?regione ?collegio ?tipo ?inizio ?fine ?fineTipo WHERE {
 ?s a osr:Senatore ; foaf:lastName ?cognome ; foaf:firstName ?nome ; osr:mandato ?m .
 ?m osr:legislatura ?leg ; osr:tipoMandato ?tm ; osr:inizio ?inizio . FILTER(str(?leg) = "%d" && str(?tm) = "elettivo")
 OPTIONAL { ?m osr:regioneElezione ?regione } OPTIONAL { ?m osr:collegioElezione ?collegio }
 OPTIONAL { ?m osr:tipoElezione ?tipo } OPTIONAL { ?m osr:fine ?fine } OPTIONAL { ?m osr:tipoFineMandato ?fineTipo }
 OPTIONAL { ?s osr:dataNascita ?nascita } OPTIONAL { ?s foaf:gender ?genere }
}"""
DAYS = [
    date(1948, 4, 18),
    date(1953, 6, 7),
    date(1958, 5, 25),
    date(1963, 4, 28),
    date(1968, 5, 19),
    date(1972, 5, 7),
    date(1976, 6, 20),
    date(1979, 6, 3),
    date(1983, 6, 26),
    date(1987, 6, 14),
    date(1992, 4, 5),
    date(1994, 3, 27),
    date(1996, 4, 21),
    date(2001, 5, 13),
    date(2006, 4, 9),
    date(2008, 4, 13),
    date(2013, 2, 24),
    date(2018, 3, 4),
    date(2022, 9, 25),
]
MATTARELLA = date(1994, 3, 27)
CALDEROLI = date(2006, 4, 9)
ROSATO = date(2018, 3, 4)
REDUCED = date(2022, 9, 25)
TRASPARENZA = (
    "https://dait.interno.gov.it/documenti/trasparenza/POLITICHE_20220925/SENATO_ITALIA_20220925/"
    "SENATO_ITALIA_20220925_pluri.json"
)
BASE = "https://elezionistorico.interno.gov.it/"
ABROAD = {
    "EUROPA": ("europe", "Europe"),
    "AMERICA MERIDIONALE": ("south-america", "South America"),
    "AMERICA SETTENTRIONALE E CENTRALE": ("north-and-central-america", "North and Central America"),
    "AFRICA ASIA OCEANIA ANTARTIDE": ("africa-asia-oceania-antarctica", "Africa, Asia, Oceania, and Antarctica"),
    "ASIA AFRICA OCEANIA ANTARTIDE": ("africa-asia-oceania-antarctica", "Africa, Asia, Oceania, and Antarctica"),
}
CENTER_LEFT = ("Center-left coalition", "Coalizione di centrosinistra")
CENTER_RIGHT = ("Center-right coalition", "Coalizione di centrodestra")
LEADERS = {
    (2006, "PRODI ROMANO"): ("The Union", "L'Unione"),
    (2006, "BERLUSCONI SILVIO"): ("House of Freedoms", "Casa delle Libertà"),
    (2008, "SILVIO BERLUSCONI"): CENTER_RIGHT,
    (2008, "WALTER VELTRONI"): CENTER_LEFT,
    (2013, "PIER LUIGI BERSANI"): ("Italy. Common Good", "Italia. Bene Comune"),
    (2013, "SILVIO BERLUSCONI"): CENTER_RIGHT,
    (2018, CENTER_RIGHT[0]): CENTER_RIGHT,
    (2018, CENTER_LEFT[0]): CENTER_LEFT,
    (2022, CENTER_RIGHT[0]): CENTER_RIGHT,
    (2022, CENTER_LEFT[0]): CENTER_LEFT,
    (2022, "Campobase"): ("Campobase", "Campobase"),
}
JOINT = {
    "LEGA PER SALVINI PREM.-FORZA IT.-NOI MODERATI-FRAT. D'ITALIA": (
        CENTER_RIGHT[0],
        [
            "LEGA PER SALVINI PREMIER",
            "FORZA ITALIA",
            "NOI MODERATI/LUPI - TOTI - BRUGNARO - UDC",
            "FRATELLI D'ITALIA CON GIORGIA MELONI",
        ],
    ),
    "PARTITO DEMOCRATICO - +EUROPA - ALLEANZA VERDI E SINISTRA": (
        CENTER_LEFT[0],
        ["PARTITO DEMOCRATICO - ITALIA DEMOCRATICA E PROGRESSISTA", "+EUROPA", "ALLEANZA VERDI E SINISTRA"],
    ),
    "CAMPOBASE +EUROPA-ALL.VERDI E SIN.? PD-AZIONE-ITALIA VIVA": (
        "Campobase",
        [
            "PARTITO DEMOCRATICO - ITALIA DEMOCRATICA E PROGRESSISTA",
            "+EUROPA",
            "ALLEANZA VERDI E SINISTRA",
            "AZIONE - ITALIA VIVA - CALENDA",
        ],
    ),
}
SIZES = [
    (237, date(1948, 5, 8)),
    (246, date(1958, 6, 12)),
    (315, date(1963, 5, 16)),
    (200, date(2022, 10, 13)),
]
REGIONS = {
    "PIEMONTE": "01",
    "VALLE D AOSTA": "02",
    "LOMBARDIA": "03",
    "TRENTINO ALTO ADIGE": "04",
    "VENETO": "05",
    "FRIULI VENEZIA GIULIA": "06",
    "LIGURIA": "07",
    "EMILIA ROMAGNA": "08",
    "TOSCANA": "09",
    "UMBRIA": "10",
    "MARCHE": "11",
    "LAZIO": "12",
    "ABRUZZO": "13",
    "ABRUZZI": "13",
    "MOLISE": "14",
    "ABRUZZI E MOLISE": "13-14",
    "CAMPANIA": "15",
    "PUGLIA": "16",
    "BASILICATA": "17",
    "CALABRIA": "18",
    "SICILIA": "19",
    "SARDEGNA": "20",
}
INDEPENDENT = {"IND", "INDIPENDENTE"}
REGISTERED, VOTERS, BLANK, VOTES = 2, 14, 6, 1
SENATE_2005 = [
    {"percent": 20, "level": "coalition", "requires": {"percent": 3}},
    {"percent": 3, "level": "list", "members": "coalition"},
    {"percent": 8, "level": "list", "members": "standalone"},
]
SYSTEMS = {
    "senato-1948": (
        {"formula": "dhondt", "lists": {"order": "open", "constituencies": "skip", "vacancy": "next_by_votes"}},
        "Senate, single-member collegi with regional groups, 1948–1993",
        (
            "Under law 29 of 1948, each region was divided into single-member collegi. A candidate who won at least 65% "
            "of the valid votes in a collegio was elected outright. The region's other seats went to groups of "
            "candidates who ran under the same symbol, shared by the D'Hondt method on each group's votes across the "
            "region, less the votes of its candidates already elected outright. Within a group, seats went to the "
            "candidates with the highest share of the vote in their own collegio, so a collegio could elect several "
            "senators or none. The schema stores the D'Hondt step; the 65% rule and the ranking by share are described "
            "here only."
        ),
    ),
    "senato-single-member-1993": (
        {"formula": "fptp"},
        "Senate, single-member collegi, 1993–2005",
        None,
    ),
    "senato-recovery-1993": (
        {"formula": "dhondt", "lists": {"order": "open", "constituencies": "skip", "vacancy": "next_by_votes"}},
        "Senate, regional proportional recovery, 1993–2005",
        (
            "Under law 276 of 1993, three quarters of each region's senators were elected by plurality in single-member "
            "collegi. The rest were shared out in each region by the D'Hondt method among groups of candidates who ran "
            "under the same symbol. A group's total was the votes of its candidates who had not won their collegio "
            "(scorporo), and its seats went to those candidates in order of their share of the vote in their own "
            "collegio. The schema stores the D'Hondt step; the scorporo and the ranking by share are described here only."
        ),
    ),
    "senato-valle-d-aosta": ({"formula": "fptp"}, "Senate, Valle d'Aosta single-member seat", None),
    "senato-proportional-2005": (
        {
            "formula": "hare",
            "pooling": "coalition",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": SENATE_2005,
            "bonus": {
                "level": "coalition",
                "size": {"kind": "target", "fraction": 0.55, "rounding": "up"},
                "remainder": "others",
            },
        },
        "Senate, regional lists with majority bonus, 2005–2017",
        (
            "Under law 270 of 2005, senators were elected region by region from closed lists. Coalitions needed 20% of "
            "the region's valid votes and their lists 3%; lists running alone, or in a coalition below 20%, needed 8%. "
            "Seats were shared among coalitions and lone lists by the largest remainder of a Hare quota, counting all "
            "the votes of a coalition's lists, and then among each coalition's qualifying lists in the same way. If the "
            "coalition or list with the most votes in the region won less than 55% of its seats, it received 55%, "
            "rounded up, and the others shared the rest. Molise, with two seats, had no bonus. Valle d'Aosta elected "
            "one senator by plurality, and Trentino-Alto Adige kept the single-member collegi of 1993. The schema "
            "stores the Hare step; the thresholds and the bonus are described here only."
        ),
    ),
    "senato-molise-2005": (
        {
            "formula": "hare",
            "pooling": "coalition",
            "lists": {"order": "closed", "vacancy": "next_on_list"},
            "threshold": SENATE_2005,
        },
        "Senate, Molise regional lists, 2005–2017",
        None,
    ),
    "senato-trentino-2005": (
        {"formula": "fptp"},
        "Senate, Trentino-Alto Adige single-member collegi, 2005–2017",
        None,
    ),
    "senato-trentino-recovery-2005": (
        {"formula": "dhondt", "lists": {"order": "open", "constituencies": "skip", "vacancy": "next_by_votes"}},
        "Senate, Trentino-Alto Adige proportional seat, 2005–2017",
        (
            "Under law 270 of 2005, Trentino-Alto Adige kept the rules of law 276 of 1993: six senators were elected by "
            "plurality in single-member collegi and one was shared out by the D'Hondt method among groups of candidates "
            "who ran under the same symbol, counting the votes of their candidates who had not won their collegio. "
            "The seat went to the group's candidate with the highest share of the vote in their own collegio. The "
            "schema stores the D'Hondt step; the scorporo and the ranking by share are described here only."
        ),
    ),
    "senato-single-member-2017": (
        {"formula": "fptp"},
        "Senate, single-member collegi, since 2017",
        None,
    ),
    "senato-proportional-2017": (
        {
            "formula": "hare",
            "pooling": "coalition",
            "lists": {"order": "closed", "constituencies": "skip", "vacancy": "next_on_list"},
        },
        "Senate, regional lists, since 2017",
        (
            "Under law 165 of 2017, about three eighths of the senators are elected by plurality in single-member "
            "collegi and the rest from short closed lists in multi-member collegi. Each voter casts one vote: a vote "
            "for a list also counts for the single-member candidate it supports, and a vote for the candidate alone "
            "is shared among the candidate's lists in proportion to their votes in the collegio, so the list votes "
            "shown include those shares. Lists need 3% of the national valid vote, or 20% in their region, and "
            "coalitions need 10% nationally. A coalition list between 1% and 3% adds its votes to the coalition but "
            "wins no seats. Seats are shared region by region among qualifying coalitions and lists by the largest "
            "remainder of a Hare quota, then among each coalition's qualifying lists in the same way, and then among "
            "the region's multi-member collegi. Valle d'Aosta elects one senator by plurality, and Trentino-Alto "
            "Adige elects six in single-member collegi (and in 2018 one more from lists). The Senate had 315 elected "
            "seats until 2022, and 200 since. The schema stores the Hare step; the thresholds and the split among "
            "collegi are described here only."
        ),
    ),
    "senato-estero": (
        {"formula": "hare", "lists": {"order": "open", "vacancy": "next_by_votes"}},
        "Senate, constituency abroad",
        (
            "Since 2006, Italians resident abroad elect their own senators in four ripartizioni: Europe; South "
            "America; North and Central America; and Africa, Asia, Oceania, and Antarctica. Until 2018 they elected "
            "six (two each in Europe and South America), and since 2022 four, one in each. Seats in each ripartizione "
            "go to lists by the largest remainder of a Hare quota, and to candidates by preference votes."
        ),
    ),
}
LICENSE = (
    "No license stated. Data that Italian public bodies publish without a license are open by default (art. 52 of "
    "the Codice dell'amministrazione digitale)."
)
BODY_NOTE = (
    "Seats count the elected senators only. Senators for life, former presidents, and in the first legislature "
    "(1948–1953) the 107 senators by right under the Constitution's third transitional provision sat in addition."
)
GROUP_NOTE = (
    "Seats are each group's seats in the region as Eligendo's historical results give them. Who took them is the "
    "Interior Ministry's archive, whose candidates carry an elected flag."
)
REPLACED_NOTE = (
    "The Interior Ministry's archive flags {flagged} elected candidates in this region, but the region elected "
    "{declared} senators. The elected senators here are the ones the Senate's own record lists at the legislature's "
    "first sitting, matched to the archive's candidates by name."
)
UNPLACED_NOTE = (
    "The archive has no candidate names for {label} in some of this region's collegi, so {names} appear only in "
    "this regional allocation, from the Senate's record of its members; their votes are the party's votes in those "
    "collegi."
)
UNNAMED_NOTE = (
    "The archive gives this party's votes in the collegio, summed from its municipal returns, but not its "
    "candidate's name."
)
OPTED_NOTE = (
    "Eligendo's results also mark {names} as elected here. Each took a seat for another region or the Chamber of "
    "Deputies, and the next candidates on the list took their places, so the elected senators shown are the ones "
    "the Senate's record lists at the legislature's first sitting."
)
REPLACED_SAME_DAY = (
    "The Senate's record also lists {names}, who replaced an elected senator on the legislature's first day; the "
    "outcome counts the senator who was proclaimed."
)
ABROAD_GAP = (
    "The votes shown by country add up to {counted} valid votes, but Eligendo gives the ripartizione {total}, and its "
    "seats were allocated on that total."
)
SENATE_NOTE = (
    "Votes are the Interior Ministry's totals, from its open-data archive and Eligendo's historical results. "
    "Elected senators are linked to the Senate's own record of its members."
)
UNORDERED_NOTE = (
    "The Interior Ministry's archive lists each list's candidates but not in ballot order, and Eligendo has no "
    "candidate pages for this election, so list positions are not given."
)
SHARES_NOTE = (
    "List votes include each list's share of the votes cast for its single-member candidates alone, as the "
    "Interior Ministry counts them."
)
VACANT = {
    (date(2018, 3, 4), "19"): (
        1,
        (
            "Sicily had 16 list seats, but only 15 were filled at the proclamation. Movimento 5 Stelle won one more "
            "seat than it had candidates left, because all the others on its Sicilian lists had won single-member "
            "collegi. On July 31, 2019, the Senate gave the seat to Emma Pavanelli, the first candidate not elected on "
            "the party's list in Umbria."
        ),
    ),
}
CAVEATS = {
    (date(2022, 9, 25), "14"): (
        "Molise had one list seat. Seats are allocated first among coalitions. The center-right coalition had the "
        "most votes, so the seat went to it. Within that coalition, the seat went to Fratelli d'Italia, although "
        "Movimento 5 Stelle had the most votes of any single list."
    ),
    (date(2018, 3, 4), "18"): (
        "On July 31, 2019, the Senate annulled Matteo Salvini's election in Calabria and gave the seat to Fulvia "
        "Michela Caligiuri of Forza Italia. Salvini sat for Lazio instead."
    ),
    (date(2018, 3, 4), "12"): (
        "On July 31, 2019, the Senate declared Matteo Salvini elected in Lazio, after annulling his election in "
        "Calabria, in place of Kristalia Rachele Papaevangeliu."
    ),
    (date(2018, 3, 4), "16"): (
        "On December 2, 2021, the Senate annulled Anna Carmela Minuto's election and moved Forza Italia's seat to "
        "another collegio in the region, where Michele Boccardi took it."
    ),
    (date(2018, 3, 4), "08:emilia-romagna-u05"): (
        "On July 31, 2019, the Senate annulled Edoardo Patriarca's election and declared Stefano Corti of the Lega "
        "elected for this collegio."
    ),
    (date(2018, 3, 4), "south-america"): (
        "On December 2, 2021, the Senate declined to validate Adriano Cario's election, and on January 12, 2022, "
        "Fabio Porta of the Partito Democratico took the seat."
    ),
}
ROMAN = re.compile(r"\b(I|Ii|Iii|Iv|V|Vi|Vii|Viii|Ix|X|Xi|Xii)\b")


def fold(text: str) -> str:
    text = unicodedata.normalize("NFKD", text or "").encode("ascii", "ignore").decode().upper()
    return " ".join(re.sub(r"[^A-Z0-9 ]", " ", text).split())


def label_of(text: str) -> str:
    return ROMAN.sub(lambda match: match[1].upper(), proper(text).replace(" - ", "–"))


def region_of(text: str) -> str:
    folded = fold(text)
    return REGIONS.get(folded) or REGIONS[next(name for name in REGIONS if folded.startswith(name))]


def count(column: str) -> pl.Expr:
    return pl.col(column).str.replace(",", ".").cast(pl.Float64).cast(pl.Int64)


def mandates(legislature: int) -> str:
    return "https://dati.senato.it/sparql?" + urlencode({"query": QUERY % legislature, "format": "text/csv"})


def fetch() -> None:
    for legislature, day in enumerate(DAYS, 1):
        raw.fetch(SOURCE, ARCHIVE.format(day=day), suffix=".zip")
        raw.fetch(SOURCE, mandates(legislature), suffix=".csv")
        if day == REDUCED:
            raw.fetch(SOURCE, TRASPARENZA, suffix=".json")
        if day >= CALDEROLI:
            if not pages(day):
                raise LookupError(f"store Eligendo's {day} pages in the playwright MCP first")
            continue
        raw.fetch(SOURCE, NATIONAL.format(day=day), suffix=".html")
        for n in range(1, 21):
            raw.fetch(SOURCE, ELIGENDO.format(day=day, n=n), suffix=".html")


def pages(day: date) -> list[str]:
    _, index = raw._index(SOURCE)
    stamp = f"dtel={day:%d/%m/%Y}&"
    return sorted(
        url
        for url in index
        if url.startswith(BASE)
        and stamp in url
        and not (
            day >= ROSATO
            and (
                ("candidati.php" in url and "tpa=I" in url) or ("tpa=G" in url and "&ne2=" in url) or "lev2=lev" in url
            )
        )
    )


def params(url: str) -> dict[str, str]:
    return dict(part.split("=", 1) for part in url.split("?", 1)[1].split("&"))


def table(url: str) -> list[tuple[bool, list[str]]]:
    page = re.sub(r"<script.*?</script>", "", raw.cached(SOURCE, url).path.read_text(encoding="utf-8"), flags=re.DOTALL)
    found = []
    for attributes, row in re.findall(r"<tr([^>]*)>(.*?)</tr>", page[max(page.find("<tbody"), 0) :], flags=re.DOTALL):
        cells = [
            html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", cell.replace("<br", " | <br")))).strip()
            for cell in re.findall(r"<t[hd][^>]*>(.*?)</t[hd]>", row, flags=re.DOTALL)
        ]
        found.append(("leader" in attributes, cells))
    return found


def heading(url: str) -> tuple[str, tuple[int, int, int] | None]:
    page = raw.cached(SOURCE, url).path.read_text(encoding="utf-8")
    text = html.unescape(
        re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", page, flags=re.DOTALL)))
    )
    title = re.search(r"Senato \d\d/\d\d/\d{4} (Area .+?) (?:Affluenza|Liste/Gruppi|Candidati)", text)
    counts = re.search(r"Elettori ([\d.]+) Votanti ([\d.]+) [\d,]+ % Schede Bianche ([\d.]+)", text)
    return (
        title[1] if title else "",
        None if counts is None else tuple(int(value.replace(".", "")) for value in counts.groups()),
    )


def number(text: str) -> int:
    return int(text.replace(".", "") or 0)


def eligendo(day: date) -> pl.DataFrame:
    rows = []
    for n in range(1, 21):
        page = raw.cached(SOURCE, ELIGENDO.format(day=day, n=n)).path.read_text(encoding="utf-8", errors="replace")
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", page, flags=re.DOTALL)))
        found = re.search(r"Area ITALIA Regione (.+?) Affluenza", text)
        if found is None:
            continue
        for label, votes, seats in re.findall(
            r"class='candidato'>([^<]+)</th><td[^>]*>([\d.]+)</td><td[^>]*>[\d,]+</td><td[^>]*>(\d+)</td>", page
        ):
            label = re.sub(r"\s*\(ELETTO\)$", "", label.replace("&#039;", "'").replace("&amp;", "&").strip())
            rows.append((day, region_of(found[1]), label, int(votes.replace(".", "")), int(seats)))
    frame = pl.DataFrame(rows, schema=["day", "region", "label", "votes", "seats"], orient="row")
    return frame.group_by("day", "region", "label").agg(pl.col("votes", "seats").sum())


def national(day: date) -> int:
    page = raw.cached(SOURCE, NATIONAL.format(day=day)).path.read_text(encoding="utf-8", errors="replace")
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", page))
    return int(re.search(r"TOTALI [\d.]+ (\d+)", text)[1])


def senators(legislature: int) -> pl.DataFrame:
    frame = pl.read_csv(raw.cached(SOURCE, mandates(legislature)).path, infer_schema=False)
    return frame.select(
        pl.lit(legislature).alias("legislature"),
        pl.col("s").str.extract(r"(\d+)$").alias("senator"),
        pl.col("cognome").fill_null("").alias("surname"),
        pl.col("nome").alias("forename"),
        pl.col("nascita").str.to_date(strict=False).alias("birth"),
        pl.col("genere").alias("gender"),
        pl.col("regione").fill_null("").alias("region_name"),
        pl.col("collegio").alias("collegio"),
        pl.col("tipo").alias("kind"),
        pl.col("inizio").str.to_date().alias("start"),
    ).unique(["senator", "start"])


def match(candidates: list[dict], surname: str, forename: str) -> dict | None:
    target, first = fold(f"{surname} {forename}"), (fold(surname).split() or [""])[0]
    letters = {word[0] for word in fold(forename).split()}
    best, score = None, 0.0
    for row in candidates:
        words = fold(row["surname"]).split()
        ratio = difflib.SequenceMatcher(None, target, fold(f"{row['surname']} {row['forename']}")).ratio()
        if (
            words
            and difflib.SequenceMatcher(None, first, words[0]).ratio() >= 0.85
            and (len(first) >= 4 or difflib.SequenceMatcher(None, fold(surname), fold(row["surname"])).ratio() >= 0.85)
        ):
            initials = {word[0] for word in fold(row["forename"]).split()}
            ratio += 1 + (0.5 if initials and initials <= letters | {word[0] for word in words[1:]} else 0)
        if ratio > score:
            best, score = row, ratio
    return best if score >= 0.8 and (score < 1 or score >= 1.4) else None


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    all_candidates, all_turnout, all_lists, all_seats, all_senators, all_unplaced, all_declared = (
        [],
        [],
        [],
        [],
        [],
        [],
        [],
    )
    party_lists, party_accounts, party_candidates, abroad_votes, singles, recovered = [], [], [], [], [], []
    r_lists, r_members, r_singles, r_accounts = [], [], [], []
    for legislature, day in enumerate(DAYS, 1):
        if day >= CALDEROLI:
            roll = senators(legislature)
            all_senators.append(roll)
            members = (
                roll.filter(pl.col("start") <= roll["start"].min() + timedelta(days=7)).unique("senator").to_dicts()
            )
            for row in members:
                folded = fold(row["region_name"])
                row["seat"] = ABROAD[folded][0] if folded in ABROAD else region_of(row["region_name"])
            with zipfile.ZipFile(raw.cached(SOURCE, ARCHIVE.format(day=day)).path) as archive:
                member = next(name for name in archive.namelist() if "candlista" in name.lower())
                cand = pl.read_csv(
                    io.BytesIO(archive.read(member)),
                    separator=";",
                    infer_schema=False,
                    encoding="utf8" if day >= ROSATO else "latin1",
                ).rename({"Regione": "REGIONE", "CollPlurinom": "COLLEGIOPLURINOM"}, strict=False)
            filed = {
                (
                    region_of(row["REGIONE"]),
                    fold(html.unescape(row["descrlista"])),
                    fold(f"{row['cognome']} {row['nome']}"),
                ): row
                for row in cand.with_columns(pl.col(c).str.strip_chars() for c in cand.columns).to_dicts()
            }
            coalesced: set[tuple[str, str | None]] = set()
            day_lists = []
            for url in pages(day):
                if url.startswith(BASE + "candidati.php"):
                    continue
                query = params(url)
                title, counts = heading(url)
                division = None
                if query["tpa"] == "I" and query["tpe"] == "R" and " Regione " in title and day < ROSATO:
                    division = region_of(title.split(" Regione ", 1)[1])
                    party_accounts.append((day, division, None, *counts))
                    leader = None
                    for is_leader, cells in table(url):
                        if is_leader:
                            leader = cells[0]
                        elif cells[1:2] == ["Totale coalizione"]:
                            coalesced.add((division, leader))
                        elif len(cells) >= 6 and cells[1]:
                            day_lists.append((division, cells[1], leader, number(cells[-3]), number(cells[-1])))
                elif query["tpa"] == "E" and query["tpe"] in ("T", "N"):
                    division = ABROAD[fold(title.split(" Ripartizione ", 1)[1].split(" Nazione ")[0])][0]
                    country = title.split(" Nazione ", 1)[1] if query["tpe"] == "N" else None
                    if counts is None and country is not None:
                        if any(len(cells) >= 3 and cells[1] and number(cells[2]) for _, cells in table(url)):
                            raise ValueError(f"{day} {country}: votes without turnout")
                        continue
                    party_accounts.append((day, division, country, *counts))
                    for _, cells in table(url):
                        if len(cells) < 3 or not cells[1]:
                            continue
                        if country is None:
                            day_lists.append((division, cells[1], None, number(cells[2]), number(cells[4])))
                        else:
                            abroad_votes.append((day, division, country, cells[1], number(cells[2])))
                    if country is not None:
                        continue
                elif query["tpa"] == "G" and query["tpe"] == "L" and day < ROSATO:
                    region = region_of(title.split(" Regione ", 1)[1].split(" Collegio ")[0])
                    collegio = title.split(" Collegio ", 1)[1]
                    party_accounts.append((day, region, collegio, *counts))
                    pool = [row for row in members if row["seat"] == region]
                    for _, cells in table(url):
                        if len(cells) < 3 or " | " not in cells[1]:
                            continue
                        label, person = cells[1].split(" | ", 1)
                        elected = "(ELETTO)" in person
                        person = person.replace("(ELETTO)", "").strip()
                        named = [row for row in pool if (fold(person) + " ").startswith(fold(row["surname"]) + " ")]
                        senator = (named[0] if len(named) == 1 else match(pool, person, "")) if elected else None
                        singles.append(
                            (
                                day,
                                region,
                                collegio,
                                label,
                                person,
                                number(cells[2]),
                                elected,
                                senator and senator["senator"],
                            )
                        )
                if division is None:
                    continue
                listed = []
                for link, label in dict(
                    re.findall(
                        r"carica_candidati\('" + re.escape(BASE) + r"(candidati\.php\?[^']+)'[^>]*?title=\"([^\"]+)\"",
                        raw.cached(SOURCE, url).path.read_text(encoding="utf-8"),
                    )
                ).items():
                    label = html.unescape(label).strip()
                    rows = [cells for _, cells in table(BASE + html.unescape(link)) if cells[0] != "Candidati"]
                    for position, cells in enumerate(rows, 1):
                        found = filed.get((division, fold(label), fold(cells[0])))
                        listed.append(
                            {
                                "day": day,
                                "region": division,
                                "label": label,
                                "position": position if query["tpa"] == "I" else None,
                                "name": cells[0],
                                "surname": found["cognome"] if found else None,
                                "forename": found["nome"] if found else None,
                                "birth": datetime.strptime(cells[1], "%d/%m/%Y").date()
                                if re.fullmatch(r"\d\d/\d\d/\d{4}", cells[1])
                                else None,
                                "sex": found and found["sesso"],
                                "proclaimed": "Eletto" in cells[3:],
                                "elected": False,
                                "replacement": False,
                                "senator": None,
                                "preferences": number(cells[3]) if query["tpa"] == "E" else None,
                            }
                        )
                pool = [row | {"surname": row["name"], "forename": "", "i": i} for i, row in enumerate(listed)]
                for senator in members:
                    if senator["seat"] != division:
                        continue
                    surname, forename = fold(senator["surname"]), (fold(senator["forename"]).split() or [""])[0]
                    exact = [
                        row
                        for row in pool
                        if (fold(row["name"]) + " ").startswith(surname + " ")
                        and forename in fold(row["name"]).removeprefix(surname).split()
                        and not listed[row["i"]]["elected"]
                    ]
                    exact.sort(key=lambda row: not row["proclaimed"])
                    named = [
                        row
                        for row in pool
                        if (fold(row["name"]) + " ").startswith(surname + " ")
                        and row["proclaimed"]
                        and not listed[row["i"]]["elected"]
                    ]
                    found = (
                        exact[0]
                        if exact
                        else named[0]
                        if len(named) == 1
                        else match(pool, senator["surname"], senator["forename"])
                    )
                    if found is None:
                        all_unplaced.append({"day": day, "region": division, "senator": senator["senator"]})
                        continue
                    row = listed[found["i"]]
                    row |= {"elected": True, "senator": senator["senator"]}
                    if row["surname"] is None:
                        row |= {"surname": senator["surname"].upper(), "forename": senator["forename"].upper()}
                seats_of = {label: seats for region, label, _, _, seats in day_lists if region == division}
                for label in {row["label"] for row in listed}:
                    seated = [row for row in listed if row["label"] == label and row["elected"]]
                    for row in sorted(seated, key=lambda row: (row["proclaimed"], -(row["position"] or 0)))[
                        : max(len(seated) - seats_of.get(label, 0), 0)
                    ]:
                        row |= {"elected": False, "replacement": True}
                party_candidates.extend(listed)
            for region, label, leader, votes, seats in day_lists:
                party_lists.append(
                    {
                        "day": day,
                        "region": region,
                        "label": label,
                        "coalition": leader if (region, leader) in coalesced else None,
                        "votes": votes,
                        "seats": seats,
                    }
                )
            day_singles = [row for row in singles if row[0] == day]
            for row in day_singles:
                if row[6] and row[5] < max(other[5] for other in day_singles if other[2] == row[2]):
                    recovered.append((day, row[1], row[3], row[7]))
            if day >= ROSATO:
                with zipfile.ZipFile(raw.cached(SOURCE, ARCHIVE.format(day=day)).path) as archive:

                    def sheet(pattern: str) -> pl.DataFrame:
                        member = next(name for name in archive.namelist() if re.search(pattern, name))
                        frame = pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema=False)
                        return frame.with_columns(pl.col(c).str.strip_chars() for c in frame.columns)

                    region = pl.col("REGIONE").map_elements(region_of, return_dtype=pl.String)
                    born = pl.col("birth").str.split(" ").list.first().str.to_date("%d/%m/%Y", strict=False)
                    if day < REDUCED:
                        plural = pl.concat_str(
                            pl.col("plural").str.extract(r"^(.+) - \d+$"),
                            pl.lit(" - P"),
                            pl.col("plural").str.extract(r" - (\d+)$"),
                        )
                        single = pl.concat_str(
                            pl.col("REGIONE"),
                            pl.lit(" - U"),
                            pl.col("single").str.extract(r"^(\d+) - "),
                            pl.lit(" ("),
                            pl.col("single").str.extract(r"^\d+ - (.+)$"),
                            pl.lit(")"),
                        )
                        day_lists = pl.concat(
                            [
                                sheet("Liste_Italia_Liv_Reg").select(
                                    region=region,
                                    college=pl.lit(None, pl.String),
                                    label="LISTA",
                                    votes=count("VOTILISTA"),
                                    seats=count("SEGGILISTA"),
                                ),
                                sheet("Liste_Italia_CollPlurinom")
                                .rename({"COLLPLURINOM": "plural"})
                                .select(
                                    region=region,
                                    college=plural,
                                    label="LISTA",
                                    votes=count("TOTVOTI"),
                                    seats=count("SEGGILISTA"),
                                ),
                            ]
                        )
                        day_members = (
                            sheet("CandidDiListaCollPlurinom")
                            .rename({"COLLEGIOPLURINOM": "plural", "datanascita": "birth"})
                            .select(
                                region=region,
                                college=plural,
                                label="LISTA",
                                position=pl.lit(None, pl.Int64),
                                surname="cognome",
                                forename="nome",
                                birth=born,
                                sex="sesso",
                                elected=pl.col("CODTIPOELETTO") == "E",
                            )
                        )
                        uninominal = sheet("Liste&CandidCollUninom").rename(
                            {"COLLEGIOPLURINOM": "plural", "COLLEGIOUNINOM": "single", "DATANASCITA": "birth"}
                        )
                        aosta = sheet("VAosta_CandidCollUninom")
                        day_singles = pl.concat(
                            [
                                uninominal.group_by(
                                    "REGIONE",
                                    "plural",
                                    "single",
                                    "COGNOME",
                                    "NOME",
                                    "birth",
                                    "SESSO",
                                    "CODTIPOELETTO",
                                    "VOTICAND",
                                )
                                .agg(pl.col("LISTA").drop_nulls().unique().sort().alias("lists"))
                                .select(
                                    region=region,
                                    college=single,
                                    parent=plural,
                                    surname="COGNOME",
                                    forename="NOME",
                                    birth=born,
                                    sex="SESSO",
                                    votes=count("VOTICAND"),
                                    lists="lists",
                                    flagged=pl.col("CODTIPOELETTO") == "E",
                                ),
                                aosta.select(
                                    region=pl.lit("02"),
                                    college=pl.col("COLLEGIOUNINOM").str.replace(r" - (\d+)$", " - U$1"),
                                    parent=pl.lit(None, pl.String),
                                    surname="COGNOME",
                                    forename="NOME",
                                    birth=pl.col("DATANASCITA")
                                    .str.split(" ")
                                    .list.first()
                                    .str.to_date("%d/%m/%Y", strict=False),
                                    sex="SESSO",
                                    votes=count("TOTVOTI"),
                                    lists=pl.concat_list(pl.col("LISTA")),
                                    flagged=pl.col("CODTIPOELETTO") == "E",
                                ),
                            ]
                        )
                        accounting = [
                            (sheet("Scrutini_Italia_Liv_Reg"), "REGIONE", pl.lit(None, pl.String)),
                            (
                                sheet("Scrutini_Italia_CollPlurinom").rename({"COLLPLURINOM": "plural"}),
                                "REGIONE",
                                plural,
                            ),
                            (
                                sheet("Scrutini_Italia_CollUninom").rename(
                                    {"COLLEGIOUNINOM": "single", "ELETTORITOTALI": "NUMELETTORITOTALI"}
                                ),
                                "REGIONE",
                                single,
                            ),
                            (
                                sheet("Scrutini_VAosta_LivCollUninom").with_columns(
                                    pl.lit("VALLE D'AOSTA").alias("REGIONE")
                                ),
                                "REGIONE",
                                pl.col("COLLEGIO").str.replace(r" - (\d+)$", " - U$1"),
                            ),
                        ]
                        day_accounts = pl.concat(
                            [
                                frame.rename({"ELETTORITOTALI": "NUMELETTORITOTALI"}, strict=False).select(
                                    region=region,
                                    college=college,
                                    registered=count("NUMELETTORITOTALI"),
                                    voters=count("NUMVOTANTITOTALI"),
                                    blank=count("SKBIANCHE"),
                                )
                                for frame, _, college in accounting
                            ]
                        )
                    else:
                        communes = sheet("Senato_Italia_LivComune").rename(
                            {"CIRC-REG": "REGIONE", "COLLPLURI": "college", "COLLUNINOM": "single"}
                        )
                        towns = communes.unique(["single", "COMUNE"])
                        small = sheet(r"VAosta&Trentino_LivComune\.csv").with_columns(
                            pl.concat_str(
                                pl.col("REGIONE").str.split("/").list.first(), pl.lit(" - "), "COLLEGIO"
                            ).alias("single")
                        )
                        small_towns = sheet("VAosta&Trentino_livComune_Scrutini").with_columns(
                            pl.concat_str(
                                pl.col("REGIONE").str.split("/").list.first(), pl.lit(" - "), "COLLEGIO"
                            ).alias("single")
                        )
                        list_votes = communes.unique(["single", "COMUNE", "DESCRLISTA"]).with_columns(
                            count("VOTILISTA").alias("votes")
                        )
                        positions = {
                            (
                                fold(row["desc_ente"]),
                                fold(row["desc_lista"]),
                                fold(row["cogn_c"]),
                                fold(row["nome_c"]),
                            ): row["num_c"]
                            for row in json.loads(raw.cached(SOURCE, TRASPARENZA).path.read_text(encoding="utf-8-sig"))[
                                "candidati"
                            ]
                        }
                        day_members = cand.rename({"datanascita": "birth"}).select(
                            region=region,
                            college="COLLEGIOPLURINOM",
                            label="descrlista",
                            position=pl.struct("COLLEGIOPLURINOM", "descrlista", "cognome", "nome").map_elements(
                                lambda row, positions=positions: positions[
                                    fold(row["COLLEGIOPLURINOM"]),
                                    fold(row["descrlista"]),
                                    fold(row["cognome"]),
                                    fold(row["nome"]),
                                ],
                                return_dtype=pl.Int64,
                            ),
                            surname="cognome",
                            forename="nome",
                            birth=born,
                            sex="sesso",
                            elected=pl.col("CODTIPOELETTO") == "E",
                        )
                        won = day_members.filter("elected").group_by("region", "college", "label").len("seats")
                        plural_lists = (
                            list_votes.group_by("REGIONE", "college", "DESCRLISTA")
                            .agg(pl.col("votes").sum())
                            .select(region=region, college="college", label="DESCRLISTA", votes="votes")
                        )
                        plural_lists = plural_lists.join(won, on=["region", "college", "label"], how="left")
                        day_lists = pl.concat(
                            [
                                plural_lists.group_by("region", "label")
                                .agg(pl.col("votes").sum(), pl.col("seats").sum())
                                .select("region", pl.lit(None, pl.String).alias("college"), "label", "votes", "seats"),
                                plural_lists.select("region", "college", "label", "votes", "seats"),
                            ]
                        ).with_columns(pl.col("seats").fill_null(0).cast(pl.Int64))
                        day_singles = pl.concat(
                            [
                                communes.unique(["single", "COMUNE", "COGNOME", "NOME", "DATANASCITA"])
                                .group_by("REGIONE", "college", "single", "COGNOME", "NOME", "DATANASCITA", "SESSO")
                                .agg(count("VOTICANDIDATO").sum().alias("votes"))
                                .join(
                                    communes.group_by("single", "COGNOME", "NOME", "DATANASCITA").agg(
                                        pl.col("DESCRLISTA").drop_nulls().unique().sort().alias("lists")
                                    ),
                                    on=["single", "COGNOME", "NOME", "DATANASCITA"],
                                )
                                .select(
                                    region=region,
                                    college="single",
                                    parent="college",
                                    surname="COGNOME",
                                    forename="NOME",
                                    birth=pl.col("DATANASCITA").str.to_date("%d/%m/%Y", strict=False),
                                    sex="SESSO",
                                    votes="votes",
                                    lists="lists",
                                ),
                                small.group_by("REGIONE", "single", "COGNOME", "NOME", "DATANASCITA", "SESSO")
                                .agg(
                                    count("TOTVOTI").sum().alias("votes"),
                                    pl.col("CONTRASSEGNO").unique().sort().alias("lists"),
                                )
                                .select(
                                    region=region,
                                    college="single",
                                    parent=pl.lit(None, pl.String),
                                    surname="COGNOME",
                                    forename="NOME",
                                    birth=pl.col("DATANASCITA")
                                    .str.split(" ")
                                    .list.first()
                                    .str.to_date("%d/%m/%Y", strict=False),
                                    sex="SESSO",
                                    votes="votes",
                                    lists="lists",
                                ),
                            ]
                        ).with_columns(flagged=pl.col("votes") == pl.col("votes").max().over("region", "college"))
                        fields = [
                            count("ELETTORITOT").sum().alias("registered"),
                            count("VOTANTITOT").sum().alias("voters"),
                            count("SKBIANCHE").sum().alias("blank"),
                        ]
                        small_fields = [
                            count("NUMELETTORI").sum().alias("registered"),
                            count("VOTANTITOT").sum().alias("voters"),
                            count("SKBIANCHE").sum().alias("blank"),
                        ]
                        day_accounts = pl.concat(
                            [
                                towns.group_by("REGIONE")
                                .agg(fields)
                                .select(
                                    region=region,
                                    college=pl.lit(None, pl.String),
                                    registered="registered",
                                    voters="voters",
                                    blank="blank",
                                ),
                                towns.group_by("REGIONE", "college")
                                .agg(fields)
                                .select(
                                    region=region,
                                    college="college",
                                    registered="registered",
                                    voters="voters",
                                    blank="blank",
                                ),
                                towns.group_by("REGIONE", "single")
                                .agg(fields)
                                .select(
                                    region=region,
                                    college="single",
                                    registered="registered",
                                    voters="voters",
                                    blank="blank",
                                ),
                                small_towns.group_by("REGIONE", "single")
                                .agg(small_fields)
                                .select(
                                    region=region,
                                    college="single",
                                    registered="registered",
                                    voters="voters",
                                    blank="blank",
                                ),
                            ]
                        )
                declared, official, final, turnout = {}, {}, {}, {}
                colleges = dict(day_singles.select("college", "region").unique("college").iter_rows())
                for url in pages(day):
                    query = params(url)
                    title, counts = heading(url)
                    if "index.php" in url and (query["tpe"] == "U" or (query["tpa"] == "G" and query["tpe"] == "L")):
                        place = (
                            title.rsplit(" Collegio ", 1)[1]
                            if query["tpe"] == "U"
                            else next(
                                name
                                for name, region in colleges.items()
                                if region == f"{query['ne1'][0]:0>2}" and f" - U{query['ne1'][1:]} " in name + " "
                            )
                        )
                        turnout[colleges[place], place] = counts
                        for is_leader, cells in table(url):
                            if is_leader:
                                final[place, fold(re.sub(r" DETT[AO] .+$", "", cells[0]))] = (
                                    next(number(cell) for cell in cells[2:] if re.fullmatch(r"[\d.]+", cell)),
                                    cells[1] == "Eletto",
                                )
                        continue
                    if query["tpa"] != "I" or query["tpe"] not in ("R", "P") or " Regione " not in title:
                        continue
                    place = title.split(" Regione ", 1)[1].split(" Collegio Plurinominale ")
                    where = (
                        region_of(place[0]),
                        re.sub(
                            r" - (\d+)$",
                            r" - P\1",
                            unicodedata.normalize("NFKD", place[1]).encode("ascii", "ignore").decode(),
                        )
                        if query["tpe"] == "P"
                        else None,
                    )
                    for is_leader, cells in table(url):
                        if not is_leader and len(cells) >= 6 and not cells[0] and cells[1]:
                            official[*where, cells[1]] = number(cells[-3])
                            if query["tpe"] == "R" and number(cells[-1]):
                                declared[where[0], cells[1]] = number(cells[-1])
                    turnout[where] = counts
                if day >= REDUCED:
                    keys = set(day_accounts.select("region", "college").rows())
                    if keys != set(turnout):
                        raise ValueError(
                            f"{day}: turnout keys differ: {sorted(keys - set(turnout), key=str)[:3]} "
                            f"{sorted(set(turnout) - keys, key=str)[:3]}"
                        )
                    day_accounts = pl.DataFrame(
                        [(*key, *value) for key, value in turnout.items()],
                        schema=["region", "college", "registered", "voters", "blank"],
                        orient="row",
                    )
                    picks = [
                        final.get((row["college"], fold(f"{row['surname']} {row['forename']}")))
                        for row in day_singles.iter_rows(named=True)
                    ]
                    if None in picks or len(picks) != len(final):
                        raise ValueError(f"{day}: single-member candidates differ from Eligendo's collegio pages")
                    day_singles = day_singles.with_columns(
                        pl.Series("votes", [votes for votes, _ in picks]),
                        pl.Series("flagged", [won for _, won in picks]),
                    )
                    day_lists = day_lists.with_columns(
                        pl.struct("region", "college", "label")
                        .map_elements(
                            lambda row, official=official: official.get((row["region"], row["college"], row["label"])),
                            return_dtype=pl.Int64,
                        )
                        .alias("votes")
                    )
                differ = set(official.items()) ^ {
                    ((region, place, label), votes)
                    for region, place, label, votes in day_lists.select(
                        "region", "college", "label", "votes"
                    ).iter_rows()
                }
                if differ:
                    raise ValueError(f"{day}: list votes differ from Eligendo's pages: {sorted(differ, key=str)[:8]}")
                counted = {
                    (region, label): seats
                    for region, label, seats in day_lists.filter(pl.col("college").is_null() & (pl.col("seats") > 0))
                    .select("region", "label", "seats")
                    .iter_rows()
                }
                if counted != declared:
                    raise ValueError(f"{day}: list seats differ from Eligendo's regional pages")
                if day_members.filter("elected").height != day_lists.filter(pl.col("college").is_null())["seats"].sum():
                    raise ValueError(f"{day}: elected list candidates differ from list seats")
                coalition: dict[str, str] = {}
                for lists in day_singles["lists"].to_list():
                    if len(lists) < 2:
                        continue
                    english = (
                        CENTER_RIGHT[0]
                        if "FORZA ITALIA" in lists
                        else CENTER_LEFT[0]
                        if any(label.startswith("PARTITO DEMOCRATICO") for label in lists)
                        else None
                    )
                    if english is None:
                        raise ValueError(f"{day}: unnamed coalition {lists}")
                    coalition |= dict.fromkeys(lists, english)
                seated = {row["senator"] for row in members}
                picked: set[str] = set()

                def senator_of(
                    region: str, kind: str, surname: str, forename: str, day=day, members=members, picked=picked
                ) -> str | None:
                    pool = [
                        row
                        for row in members
                        if row["seat"] == region and kind in (row["kind"] or "") and row["senator"] not in picked
                    ]
                    found = match(pool, surname, forename)
                    if found is None:
                        print(f"{day} {region}: no seated senator for {surname} {forename}")
                        return None
                    picked.add(found["senator"])
                    return found["senator"]

                day_members = day_members.with_columns(
                    pl.struct("region", "surname", "forename", "elected")
                    .map_elements(
                        lambda row: (
                            senator_of(row["region"], "proporzionale", row["surname"], row["forename"])
                            if row["elected"]
                            else None
                        ),
                        return_dtype=pl.String,
                    )
                    .alias("senator")
                )
                day_singles = day_singles.with_columns(
                    pl.struct("region", "surname", "forename", "flagged")
                    .map_elements(
                        lambda row: (
                            senator_of(row["region"], "uninominale", row["surname"], row["forename"])
                            if row["flagged"]
                            else None
                        ),
                        return_dtype=pl.String,
                    )
                    .alias("senator")
                ).with_columns(elected=pl.col("flagged"))
                unseated = sorted(seated - picked - {row["senator"] for row in members if not row["seat"][0].isdigit()})
                if unseated:
                    print(f"{day}: seated senators matched to no candidate: {unseated}")
                if day_singles.group_by("college").agg(pl.col("elected").sum()).filter(pl.col("elected") != 1).height:
                    raise ValueError(f"{day}: a single-member collegio without exactly one winner")
                r_lists.append(
                    day_lists.with_columns(
                        pl.lit(day).alias("day"),
                        pl.col("label")
                        .replace_strict(coalition, default=None, return_dtype=pl.String)
                        .alias("coalition"),
                    )
                )
                r_members.append(day_members.with_columns(pl.lit(day).alias("day")))
                r_singles.append(day_singles.with_columns(pl.lit(day).alias("day")))
                r_accounts.append(day_accounts.with_columns(pl.lit(day).alias("day")))
            continue
        with zipfile.ZipFile(raw.cached(SOURCE, ARCHIVE.format(day=day)).path) as archive:
            names = archive.namelist()
            candlista = next(name for name in names if "candlista" in name.lower())
            communes = next(name for name in names if re.search(r"senato-\d{8}\.txt$", name, re.IGNORECASE))

            def read(member: str) -> pl.DataFrame:
                frame = pl.read_csv(io.BytesIO(archive.read(member)), separator=";", infer_schema=False)
                return frame.with_columns(pl.col(c).str.strip_chars() for c in frame.columns)

            cand = read(candlista)
            comm = read(communes)
        cand = cand.select(
            pl.lit(day).alias("day"),
            pl.col("REGIONE").map_elements(region_of, return_dtype=pl.String).alias("region"),
            pl.col("COLLEGIO").alias("collegio"),
            pl.col("descrlista").alias("label"),
            pl.col("votiLista").cast(pl.Int64).alias("votes"),
            pl.col("cognome").fill_null("").alias("surname"),
            pl.col("nome").fill_null("").alias("forename"),
            pl.col("datanascita").str.to_date("%d/%m/%Y", strict=False).alias("birth"),
            pl.col("sesso").alias("sex"),
            (pl.col("CODTIPOELETTO").fill_null("") == "E").alias("flagged"),
        ).with_columns(pl.col("collegio").map_elements(fold, return_dtype=pl.String).alias("key"))
        comm = comm.select(
            pl.col("REGIONE").map_elements(region_of, return_dtype=pl.String).alias("region"),
            pl.col("COLLEGIO").map_elements(fold, return_dtype=pl.String).alias("key"),
            pl.col("COMUNE").alias("commune"),
            pl.col("ELETTORI").cast(pl.Int64).alias("registered"),
            pl.col("VOTANTI").cast(pl.Int64).alias("voters"),
            pl.col("SCHEDE_BIANCHE").cast(pl.Int64).alias("blank"),
            pl.col("LISTA").alias("label"),
            pl.col("VOTI_LISTA").cast(pl.Int64).alias("votes"),
        )
        all_turnout.append(
            comm.unique(["region", "key", "commune"])
            .group_by("region", "key")
            .agg(pl.col("registered", "voters", "blank").sum())
            .with_columns(pl.lit(day).alias("day"))
        )
        all_lists.append(
            comm.group_by("region", "key", "label").agg(pl.col("votes").sum()).with_columns(pl.lit(day).alias("day"))
        )
        seats = eligendo(day)
        all_seats.append(seats)
        roll = senators(legislature)
        all_senators.append(roll)
        first = roll["start"].min()
        opening = roll.filter((pl.col("region_name") != "") & (pl.col("start") <= first + timedelta(days=7))).unique(
            "senator"
        )
        opening = opening.with_columns(
            pl.col("region_name").map_elements(region_of, return_dtype=pl.String).alias("region")
        ).with_columns(
            pl.when(pl.col("region").is_in(["13", "14"]) & (day < date(1963, 1, 1)) & (day > date(1949, 1, 1)))
            .then(pl.lit("13-14"))
            .otherwise("region")
            .alias("region")
        )
        consistent = seats.filter(pl.col("region") != "02")["seats"].sum() + 1 == national(day)
        if consistent:
            declared = dict(seats.group_by("region").agg(pl.col("seats").sum()).iter_rows())
        else:
            declared = dict(opening.group_by("region").len().iter_rows())
        elected: dict[tuple[str, str, str, str], str | None] = {}
        replaced = []
        for region in sorted(cand["region"].unique()):
            pool = cand.filter(pl.col("region") == region).to_dicts()
            flagged = [row for row in pool if row["flagged"]]
            members = [
                row
                for row in roll.filter(pl.col("region_name") != "").to_dicts()
                if region_of(row["region_name"]) in {region, *region.split("-")}
            ]
            if region == "02" or len(flagged) == declared[region]:
                for row in flagged:
                    senator = match(members, row["surname"], row["forename"]) if row["surname"] else None
                    elected[region, row["key"], row["surname"], row["forename"]] = senator and senator["senator"]
                nameless = [row for row in flagged if not row["surname"] and not row["forename"]]
                left = set(opening.filter(pl.col("region") == region)["senator"]) - set(elected.values())
                if len(nameless) == 1 and len(left) == 1:
                    row = nameless[0]
                    elected[region, row["key"], row["surname"], row["forename"]] = left.pop()
                continue
            replaced.append(region)
            for senator in opening.filter(pl.col("region") == region).to_dicts():
                row = match(pool, senator["surname"], senator["forename"]) or match(
                    [row for row in cand.to_dicts() if row["flagged"] and row["region"] != region],
                    senator["surname"],
                    senator["forename"],
                )
                if row is None:
                    all_unplaced.append({"day": day, "region": region, "senator": senator["senator"]})
                else:
                    elected[row["region"], row["key"], row["surname"], row["forename"]] = senator["senator"]
        all_declared.append(
            pl.DataFrame(
                {
                    "day": day,
                    "region": list(declared),
                    "seats": list(declared.values()),
                    "replaced": [r in replaced for r in declared],
                    "consistent": consistent,
                }
            )
        )
        cand = cand.with_columns(
            pl.struct("region", "key", "surname", "forename")
            .map_elements(
                lambda s, elected=elected: (s["region"], s["key"], s["surname"], s["forename"]) in elected,
                return_dtype=pl.Boolean,
            )
            .alias("elected"),
            pl.struct("region", "key", "surname", "forename")
            .map_elements(
                lambda s, elected=elected: elected.get((s["region"], s["key"], s["surname"], s["forename"])),
                return_dtype=pl.String,
            )
            .alias("senator"),
        )
        all_candidates.append(cand)
    pl.concat(all_candidates).write_parquet(PARSED / "candidates.parquet")
    pl.concat(all_turnout).write_parquet(PARSED / "turnout.parquet")
    pl.concat(all_lists).write_parquet(PARSED / "lists.parquet")
    pl.concat(all_seats).write_parquet(PARSED / "seats.parquet")
    pl.concat(all_senators).write_parquet(PARSED / "senators.parquet")
    pl.concat(all_declared).write_parquet(PARSED / "declared.parquet")
    pl.DataFrame(all_unplaced, schema={"day": pl.Date, "region": pl.String, "senator": pl.String}).write_parquet(
        PARSED / "unplaced.parquet"
    )
    pl.DataFrame(party_lists).write_parquet(PARSED / "party_lists.parquet")
    pl.DataFrame(party_candidates, infer_schema_length=None).write_parquet(PARSED / "party_candidates.parquet")
    pl.DataFrame(
        party_accounts, schema=["day", "region", "place", "registered", "voters", "blank"], orient="row"
    ).write_parquet(PARSED / "party_accounts.parquet")
    pl.DataFrame(abroad_votes, schema=["day", "region", "country", "label", "votes"], orient="row").write_parquet(
        PARSED / "abroad_votes.parquet"
    )
    pl.DataFrame(
        singles, schema=["day", "region", "collegio", "label", "name", "votes", "elected", "senator"], orient="row"
    ).write_parquet(PARSED / "singles.parquet")
    pl.DataFrame(recovered, schema=["day", "region", "label", "senator"], orient="row").write_parquet(
        PARSED / "recovered.parquet"
    )
    pl.concat(r_lists, how="diagonal_relaxed").write_parquet(PARSED / "rosato_lists.parquet")
    pl.concat(r_members, how="diagonal_relaxed").write_parquet(PARSED / "rosato_members.parquet")
    pl.concat(r_singles, how="diagonal_relaxed").write_parquet(PARSED / "rosato_singles.parquet")
    pl.concat(r_accounts, how="diagonal_relaxed").write_parquet(PARSED / "rosato_accounts.parquet")


def emit() -> Path:
    candidates = pl.read_parquet(PARSED / "candidates.parquet")
    turnout = pl.read_parquet(PARSED / "turnout.parquet")
    lists = pl.read_parquet(PARSED / "lists.parquet")
    seats = pl.read_parquet(PARSED / "seats.parquet")
    declared = pl.read_parquet(PARSED / "declared.parquet")
    roll = pl.read_parquet(PARSED / "senators.parquet").sort("start").unique("senator", keep="first")
    unplaced = pl.read_parquet(PARSED / "unplaced.parquet")
    party_lists = pl.read_parquet(PARSED / "party_lists.parquet")
    party_candidates = pl.read_parquet(PARSED / "party_candidates.parquet")
    party_accounts = pl.read_parquet(PARSED / "party_accounts.parquet")
    abroad_votes = pl.read_parquet(PARSED / "abroad_votes.parquet")
    single_seats = pl.read_parquet(PARSED / "singles.parquet")
    recovery = pl.read_parquet(PARSED / "recovered.parquet")
    rosato_lists = pl.read_parquet(PARSED / "rosato_lists.parquet")
    rosato_members = pl.read_parquet(PARSED / "rosato_members.parquet")
    rosato_singles = pl.read_parquet(PARSED / "rosato_singles.parquet")
    rosato_accounts = pl.read_parquet(PARSED / "rosato_accounts.parquet")
    record = {row["senator"]: row for row in roll.to_dicts()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Italian Senate elections 1948–2022: candidates and votes by collegio, region, and country abroad, list and group seats, and the elected senators",
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        def existing(scheme: str, value: str) -> UUID:
            entity = cs.key(scheme, value)
            if entity in cs.minted:
                raise ValueError(f"{scheme}:{value} is not in the database")
            return entity

        interno = cs.source("Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data")
        if interno in cs.minted:
            raise ValueError("publish it.interno before it.senato")
        senate_record = cs.source("Senato della Repubblica", "dati.senato.it: mandati dei senatori")
        if senate_record in cs.minted:
            cs.add(
                "source",
                id=senate_record,
                kind="official_body",
                publisher="Senato della Repubblica",
                title="dati.senato.it: mandati dei senatori",
                url="https://dati.senato.it/",
                license="CC BY 3.0",
            )
        for legislature, day in enumerate(DAYS, 1):
            cs.artifact(raw.cached(SOURCE, ARCHIVE.format(day=day)), interno)
            cs.artifact(raw.cached(SOURCE, mandates(legislature)), senate_record)
            if day == REDUCED:
                cs.artifact(raw.cached(SOURCE, TRASPARENZA), interno)
            if day >= CALDEROLI:
                for url in pages(day):
                    cs.artifact(raw.cached(SOURCE, url), interno)
                continue
            cs.artifact(raw.cached(SOURCE, NATIONAL.format(day=day)), interno)
            for n in range(1, 21):
                cs.artifact(raw.cached(SOURCE, ELIGENDO.format(day=day, n=n)), interno)

        italy = cs.polity("IT")
        nation = cs.area("it.nation", "IT")
        region_areas = {code: cs.area("it.regione", code) for code in set(REGIONS.values()) if "-" not in code}
        if nation in cs.minted or any(area in cs.minted for area in region_areas.values()):
            raise ValueError("publish it.istat before it.senato")
        region_names: dict[str, str] = dict(
            conn.execute(
                """select c.code, n.text from area a join area_code c on c.area_id = a.id
                   join name n on n.entity_id = a.id where a.scheme_code = 'it.regione' and n.is_preferred"""
            ).fetchall()
        )
        region_names["13-14"] = "Abruzzi e Molise"
        schemes = {code for (code,) in conn.execute("select code from scheme where code like 'it.%'").fetchall()}
        for code, rank, noun in (
            ("it.regione_senato", 1, "Senate region"),
            ("it.collegio_senato", 3, "Senate college"),
            ("it.collegio_senato_1993", 3, "Senate college"),
            ("it.collegio_senato_2005", 3, "Senate college"),
            ("it.plurinominale_senato_2017", 2, "Senate multi-member college"),
            ("it.collegio_senato_2017", 3, "Senate single-member college"),
            ("it.plurinominale_senato_2020", 2, "Senate multi-member college"),
            ("it.collegio_senato_2020", 3, "Senate single-member college"),
        ):
            if code not in schemes:
                cs.add("scheme", code=code, polity_id=italy, kind="electoral_district", rank=rank, noun=noun)
        region_areas["13-14"] = cs.area("it.regione_senato", "13-14")
        if region_areas["13-14"] in cs.minted:
            cs.add("area", id=region_areas["13-14"], scheme_code="it.regione_senato")
            cs.add(
                "area_code",
                area_id=region_areas["13-14"],
                code="13-14",
                valid_from=date(1948, 4, 18),
                valid_to=date(1963, 4, 28),
            )
            cs.add(
                "area_parent",
                area_id=region_areas["13-14"],
                parent_id=nation,
                valid_from=date(1948, 4, 18),
                valid_to=date(1963, 4, 28),
            )
            name(region_areas["13-14"], "Abruzzi e Molise", "it")

        senate = cs.key("it.body", "senate")
        if senate in cs.minted:
            cs.add("body", id=senate, polity_id=italy, kind="upper_house")
            name(senate, "Senate of the Republic")
            name(senate, "Senato della Repubblica", "it", "native")
            cs.add("note", entity_id=senate, kind="methodology", body_md=BODY_NOTE)
        sized = {row[0] for row in conn.execute("select valid_from from body_size where body_id = %s", [senate])}
        for (size, start), end in zip(SIZES, [start for _, start in SIZES[1:]] + [None], strict=True):
            if start not in sized:
                cs.add("body_size", body_id=senate, seats=size, valid_from=start, valid_to=end)

        systems: dict[str, UUID] = {}
        for code, (rules, label, methodology) in SYSTEMS.items():
            systems[code] = cs.key("it.electoral_system", code)
            if code in ("senato-proportional-2005", "senato-molise-2005") and systems[code] not in cs.minted:
                cs.add("electoral_system", id=systems[code], polity_id=italy, rules=rules, rules_version=1)
            if systems[code] in cs.minted:
                cs.add("electoral_system", id=systems[code], polity_id=italy, rules=rules, rules_version=1)
                name(systems[code], label)
                if methodology is not None:
                    cs.add("note", entity_id=systems[code], kind="methodology", body_md=methodology)

        parties: dict[tuple[str, str], UUID] = {}

        def party_of(label: str, year: int) -> UUID | None:
            if label in INDEPENDENT:
                return None
            spec = OVERRIDES.get((label, year)) or EXISTING.get(label)
            if spec is not None:
                if spec not in parties:
                    parties[spec] = existing(*spec)
                return parties[spec]
            full, abbreviation, kind = (
                NEW_OVERRIDES.get((label, year)) or NEW.get(label) or (label, None, "list_vehicle")
            )
            known = (label, year) in NEW_OVERRIDES or label in NEW
            key = ("it.party", slug(full)) if known else ("it.storico.list", slug(label))
            if key not in parties:
                parties[key] = cs.key(*key)
                if parties[key] in cs.minted:
                    cs.add("party", id=parties[key], kind=kind, polity_id=italy)
                    name(parties[key], full, "it")
                    if abbreviation is not None:
                        name(parties[key], abbreviation, "it", "abbreviation")
            return parties[key]

        coalitions = {**SYMBOLS, "POLO DELLE LIBERTA'": NORTH, "POLO BUON GOVERNO": SOUTH}
        alliances: dict[tuple[date, str], UUID] = {}

        def alliance_of(label: str, day: date, election: UUID) -> UUID | None:
            if label not in coalitions or day < MATTARELLA:
                return None
            english, italian = coalitions[label]
            if (day, english) not in alliances:
                alliances[day, english] = cs.key("it.alliance", f"{day}:{slug(english)}")
                if alliances[day, english] in cs.minted:
                    cs.add("alliance", id=alliances[day, english], kind="electoral_alliance", election_id=election)
                    name(alliances[day, english], english)
                    name(alliances[day, english], italian, "it", "native")
            return alliances[day, english]

        def value_row(result_set: UUID, unit: UUID, option: UUID | None, measure: int, value: int) -> None:
            cs.add(
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

        def result_set(key: str, election: UUID, contest: UUID, completeness: float = 1) -> UUID:
            id = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=id,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=interno,
                is_default=True,
                completeness=completeness,
            )
            return id

        known_people = {
            scheme: {value for (value,) in conn.execute("select value from external_id where scheme = %s", [scheme])}
            for scheme in ("it.senato.senatore", "it.interno.person")
        }
        held_people: set[UUID] = {
            id
            for (id,) in conn.execute(
                """select cy.person_id from candidacy cy join changeset c on c.id = cy.changeset_id
                   where c.pipeline = %(source)s
                   union select m.entity_id from entity_merge m join person p on p.id = m.into_id
                   union select x.entity_id from external_id x where x.scheme like 'it.%%'""",
                {"source": SOURCE},
            ).fetchall()
        }
        bound: dict[tuple[str, str], UUID] = {}

        def person_of(row: dict, day: date, region: str) -> UUID:
            senator = record.get(row.get("senator") or "")
            keys = []
            if senator is not None:
                keys.append(("it.senato.senatore", senator["senator"]))
            if row.get("birth") is not None:
                keys.append(("it.interno.person", f"{row['surname']}:{row['forename']}:{row['birth']}"))
            if not keys:
                keys.append((f"{SOURCE}.person", f"{day}:{region}:{row['key']}:{row['surname']}:{row['forename']}"))
            entity = next((bound[key] for key in keys if key in bound), None)
            if entity is None:
                entity = next((cs.key(*key) for key in keys if key[1] in known_people.get(key[0], ())), None)
            fresh = None
            if entity is None:
                fresh = keys[0]
                entity = cs.key(*fresh)
            if fresh is not None and entity not in held_people:
                birth = senator["birth"] if senator is not None and senator["birth"] is not None else row.get("birth")
                sex = row.get("sex") or (senator and {"male": "M", "female": "F"}.get(senator["gender"] or ""))
                cs.add(
                    "person",
                    id=entity,
                    birth_date=birth,
                    birth_precision=None if birth is None else "day",
                    gender={"M": "male", "F": "female"}.get(sex or ""),
                )
                display = (
                    f"{senator['forename']} {senator['surname']}"
                    if senator is not None
                    else f"{proper(row['forename'])} {proper(row['surname'])}".strip()
                )
                name(entity, display, "it")
            for key in keys:
                if (
                    key not in bound
                    and key != fresh
                    and key[1] not in known_people.get(key[0], ())
                    and entity not in held_people
                ):
                    cs.add("external_id", entity_id=entity, scheme=key[0], value=key[1])
                bound[key] = entity
            return entity

        colleges: dict[tuple[str, str], tuple[UUID, UUID]] = {}

        def college(
            region: str, key: str, label: str, day: date, plural: bool = False, parent: UUID | None = None
        ) -> tuple[UUID, UUID]:
            kind = "plurinominale" if plural else "collegio"
            scheme, start, end = (
                ("it.collegio_senato", DAYS[0], MATTARELLA)
                if day < MATTARELLA
                else ("it.collegio_senato_1993", MATTARELLA, CALDEROLI)
                if day < CALDEROLI
                else ("it.collegio_senato_2005", CALDEROLI, ROSATO)
                if day < ROSATO
                else (f"it.{kind}_senato_2017", ROSATO, REDUCED)
                if day < REDUCED
                else (f"it.{kind}_senato_2020", REDUCED, None)
            )
            code = f"{region}-{slug(key)}"
            if (scheme, code) in colleges:
                return colleges[scheme, code]
            area = cs.area(scheme, code, on=start)
            unit = cs.unit(scheme, code, on=start)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=code, valid_from=start, valid_to=end)
                cs.add(
                    "area_parent",
                    area_id=area,
                    parent_id=parent or region_areas[region],
                    valid_from=start,
                    valid_to=end,
                )
                name(area, label, "it")
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=italy,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=start,
                    valid_to=end,
                )
                name(unit, label, "it")
            colleges[scheme, code] = (area, unit)
            return colleges[scheme, code]

        def collegio_contest(day, election, path, region, key, rows, parent, system) -> list[dict]:
            label = label_of(rows[0]["collegio"])
            area, unit = college(region, key, label, day)
            contest_key = f"{day}:senate:{region}:{slug(key)}"
            contest = cs.key("it.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=senate,
                district_area_id=area,
                magnitude=None if parent is not None else 1,
                allocation_parent_id=parent,
                electoral_system_id=systems[system],
                default_result_kind="counted",
            )
            name(contest, f"Senate, {label}")
            cs.add("slug", path=f"{path}/senate/{slug(region_names[region])}/{slug(key)}", entity_id=contest)
            counted = result_set(contest_key, election, contest)
            accounts = (
                turnout.filter((pl.col("day") == day) & (pl.col("region") == region) & (pl.col("key") == key))
                .select("registered", "voters", "blank")
                .rows()
            )
            cap = accounts[0][1] - accounts[0][2] if accounts else None
            named = sum(row["votes"] for row in rows)
            labels = {row["label"] for row in rows}
            regional = set(candidates.filter((pl.col("day") == day) & (pl.col("region") == region))["label"])
            unnamed = [
                (label, votes)
                for label, votes in lists.filter(
                    (pl.col("day") == day)
                    & (pl.col("region") == region)
                    & (pl.col("key") == key)
                    & (pl.col("votes") > 0)
                )
                .select("label", "votes")
                .iter_rows()
                if label not in labels and label in regional
            ]
            if cap is None or named + sum(votes for _, votes in unnamed) > cap:
                unnamed = []
            if cap is not None and named + sum(votes for _, votes in unnamed) <= cap:
                registered, voters, blank = accounts[0]
                if registered >= voters:
                    value_row(counted, unit, None, REGISTERED, registered)
                value_row(counted, unit, None, VOTERS, voters)
                value_row(counted, unit, None, BLANK, blank)
            top = max(row["votes"] for row in rows)
            placed = []
            for row in sorted(rows, key=lambda row: (-row["votes"], row["surname"], row["forename"])):
                option_key = f"{contest_key}:{row['surname']}:{row['forename']}:{row['label']}"
                option = cs.key("it.option", option_key)
                party = party_of(row["label"], day.year)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party if alliance_of(row["label"], day, election) is None else None,
                    alliance_id=alliance_of(row["label"], day, election),
                    ballot_label=row["label"],
                )
                person = None
                if row["surname"] or row["forename"] or row["senator"]:
                    person = person_of(row, day, region)
                    candidacy = cs.key("it.candidacy", option_key)
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=f"{row['surname']} {row['forename']}".strip() or None,
                        is_independent=row["label"] in INDEPENDENT,
                    )
                    if party is not None and alliance_of(row["label"], day, election) is None:
                        cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party, kind="nominated")
                value_row(counted, unit, option, VOTES, row["votes"])
                wins_here = row["elected"] and (parent is not None or row["votes"] == top)
                if wins_here:
                    cs.add("outcome", result_set_id=counted, option_id=option, kind="elected")
                placed.append(
                    row | {"person": person, "single": parent is None and row["elected"] and row["votes"] == top}
                )
            for missing, votes in unnamed:
                option = cs.key("it.option", f"{contest_key}::{missing}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party_of(missing, day.year),
                    ballot_label=missing,
                )
                value_row(counted, unit, option, VOTES, votes)
                cs.add("note", entity_id=option, kind="caveat", body_md=UNNAMED_NOTE)
            return placed

        def group_contest(day, election, path, region, magnitude, system, title, members, won, note) -> UUID:
            contest_key = f"{day}:senate:{region}"
            contest = cs.key("it.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="list_allocation",
                body_id=senate,
                district_area_id=region_areas[region],
                magnitude=magnitude,
                electoral_system_id=systems[system],
                default_result_kind="counted",
            )
            name(contest, title)
            cs.add("slug", path=f"{path}/senate/{slug(region_names[region])}", entity_id=contest)
            counted = result_set(contest_key, election, contest, 0)
            for body in note:
                cs.add("note", entity_id=counted, kind="caveat", body_md=body)
            for label in sorted(set(won) | {row["label"] for row in members}):
                option = cs.key("it.option", f"{contest_key}:{label}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="list",
                    party_id=party_of(label, day.year) if alliance_of(label, day, election) is None else None,
                    alliance_id=alliance_of(label, day, election),
                    ballot_label=label,
                )
                if won.get(label):
                    cs.add("outcome", result_set_id=counted, option_id=option, kind="won", seats=won[label])
                for row in {
                    row["person"]: row for row in members if row["label"] == label and row["person"] is not None
                }.values():
                    candidacy = cs.key("it.candidacy", f"{contest_key}:{label}:{row['person']}")
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=row["person"],
                        option_id=option,
                        role="list_member",
                        ballot_name=f"{row['surname']} {row['forename']}".strip() or None,
                        is_independent=label in INDEPENDENT,
                    )
                    cs.add("outcome", result_set_id=counted, option_id=option, candidacy_id=candidacy, kind="elected")
            return contest

        def reserve(day: date, region: str) -> list[dict]:
            members = []
            for (senator,) in (
                unplaced.filter((pl.col("day") == day) & (pl.col("region") == region)).select("senator").iter_rows()
            ):
                row = record[senator]
                members.append(
                    {
                        "senator": senator,
                        "surname": row["surname"].upper(),
                        "forename": row["forename"].upper(),
                        "key": "",
                        "birth": None,
                        "sex": None,
                    }
                )
            return members

        chamber_parties: dict[tuple[date, str], UUID] = {}
        for held, label, party in conn.execute(
            """select e.date_from, o.ballot_label, o.party_id from contest_option o
               join contest c on c.id = o.contest_id join election e on e.id = c.election_id
               where e.polity_id = %s and e.date_from >= %s and c.body_id <> %s and o.kind = 'list'
               and o.party_id is not null""",
            [italy, CALDEROLI, senate],
        ).fetchall():
            chamber_parties.setdefault((held, label), party)
        divisions = {code: cs.area("it.ripartizione", code) for code, _ in ABROAD.values()}
        coalition_ids: dict[tuple[date, str], UUID] = {}
        coalition_members: set[tuple[UUID, UUID]] = set(
            conn.execute("select alliance_id, party_id from alliance_member").fetchall()
        )
        minted_countries: set[str] = set()

        def list_party(label: str, day: date) -> UUID | None:
            return chamber_parties.get((day, label)) or party_of(label, day.year)

        def coalition_of(leader: str | None, day: date, election: UUID) -> UUID | None:
            if leader is None:
                return None
            english, italian = LEADERS.get((day.year, leader)) or (
                f"{proper(leader)} coalition",
                f"Coalizione {proper(leader)}",
            )
            if (day, english) not in coalition_ids:
                coalition_ids[day, english] = cs.key("it.alliance", f"{day}:{slug(english)}")
                if coalition_ids[day, english] in cs.minted:
                    cs.add("alliance", id=coalition_ids[day, english], kind="electoral_alliance", election_id=election)
                    name(coalition_ids[day, english], english)
                    name(coalition_ids[day, english], italian, "it", "native")
            return coalition_ids[day, english]

        for day in DAYS:
            election = existing("it.election", f"{day}:general")
            path = f"it/{day}-general"
            if day >= CALDEROLI:
                day_accounts = party_accounts.filter(pl.col("day") == day)
                day_candidates = party_candidates.filter(pl.col("day") == day)
                day_lists = party_lists.filter(pl.col("day") == day)
                for region in sorted(day_lists["region"].unique()):
                    rows = day_lists.filter(pl.col("region") == region).sort("votes", descending=True).to_dicts()
                    abroad = not region[0].isdigit()
                    english = next(english for code, english in ABROAD.values() if code == region) if abroad else None
                    contest_key = f"{day}:senate:{region}"
                    contest = cs.key("it.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="body_seats" if abroad else "list_allocation",
                        body_id=senate,
                        district_area_id=divisions[region] if abroad else region_areas[region],
                        magnitude=sum(row["seats"] for row in rows),
                        electoral_system_id=systems[
                            "senato-estero"
                            if abroad
                            else "senato-molise-2005"
                            if region == "14"
                            else "senato-proportional-2005"
                        ],
                        default_result_kind="counted",
                    )
                    name(contest, f"Senate, abroad: {english}" if abroad else f"Senate, {region_names[region]}")
                    cs.add(
                        "slug",
                        path=f"{path}/senate/abroad-{region}"
                        if abroad
                        else f"{path}/senate/{slug(region_names[region])}",
                        entity_id=contest,
                    )
                    counted = result_set(contest_key, election, contest)
                    if (day, region) in CAVEATS:
                        cs.add("note", entity_id=counted, kind="caveat", body_md=CAVEATS[day, region])
                    if abroad:
                        spread = abroad_votes.filter((pl.col("day") == day) & (pl.col("region") == region))[
                            "votes"
                        ].sum()
                        total = sum(row["votes"] for row in rows)
                        if spread != total:
                            cs.add(
                                "note",
                                entity_id=counted,
                                kind="caveat",
                                body_md=ABROAD_GAP.format(counted=f"{spread:,}", total=f"{total:,}"),
                            )
                    listed = day_candidates.filter(pl.col("region") == region)
                    opted = listed.filter(pl.col("proclaimed") & ~pl.col("elected"))
                    if opted.height:
                        names = sorted(
                            f"{proper(row['forename'] or '')} {proper(row['surname'] or row['name'])}".strip()
                            for row in opted.to_dicts()
                        )
                        cs.add(
                            "note", entity_id=counted, kind="caveat", body_md=OPTED_NOTE.format(names=", ".join(names))
                        )
                    later = listed.filter(pl.col("replacement"))
                    if later.height:
                        names = sorted(
                            f"{proper(row['forename'] or '')} {proper(row['surname'] or row['name'])}".strip()
                            for row in later.to_dicts()
                        )
                        cs.add(
                            "note",
                            entity_id=counted,
                            kind="caveat",
                            body_md=REPLACED_SAME_DAY.format(names=", ".join(names)),
                        )
                    units: dict[str | None, UUID] = {}
                    for place, registered, voters, blank in (
                        day_accounts.filter((pl.col("region") == region) & (pl.col("place").is_null() != abroad))
                        .select("place", "registered", "voters", "blank")
                        .sort("place")
                        .iter_rows()
                    ):
                        if abroad:
                            code = interno_key(place)
                            units[place] = cs.unit("it.estero", code)
                            if units[place] in cs.minted and code not in minted_countries:
                                minted_countries.add(code)
                                cs.add(
                                    "reporting_unit",
                                    id=units[place],
                                    polity_id=italy,
                                    scheme_code="it.estero",
                                    code=code,
                                    area_relation="unlocated",
                                    kind="abroad_mission",
                                )
                                name(units[place], place.title().replace(" E ", " e ").replace(" Di ", " di "), "it")
                        else:
                            units[place] = cs.unit("it.regione", region)
                        if registered >= voters:
                            value_row(counted, units[place], None, REGISTERED, registered)
                        value_row(counted, units[place], None, VOTERS, voters)
                        value_row(counted, units[place], None, BLANK, blank)
                    for row in rows:
                        option = cs.key("it.option", f"{contest_key}:{slug(row['label'])}")
                        alliance = coalition_of(row["coalition"], day, election)
                        party = list_party(row["label"], day)
                        if alliance is not None and party is not None and (alliance, party) not in coalition_members:
                            coalition_members.add((alliance, party))
                            cs.add("alliance_member", alliance_id=alliance, party_id=party)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="list",
                            party_id=party,
                            alliance_id=alliance,
                            ballot_label=row["label"],
                        )
                        if abroad:
                            for country, votes in (
                                abroad_votes.filter(
                                    (pl.col("day") == day)
                                    & (pl.col("region") == region)
                                    & (pl.col("label") == row["label"])
                                )
                                .select("country", "votes")
                                .sort("country")
                                .iter_rows()
                            ):
                                value_row(counted, units[country], option, VOTES, votes)
                        else:
                            value_row(counted, units[None], option, VOTES, row["votes"])
                        if row["seats"]:
                            cs.add("outcome", result_set_id=counted, option_id=option, kind="won", seats=row["seats"])
                        for member in listed.filter(pl.col("label") == row["label"]).to_dicts():
                            member |= {
                                "surname": member["surname"] or member["name"],
                                "forename": member["forename"] or "",
                                "key": slug(row["label"]),
                            }
                            person = person_of(member, day, region)
                            candidacy = cs.key("it.candidacy", f"{contest_key}:{slug(row['label'])}:{person}")
                            cs.add(
                                "candidacy",
                                id=candidacy,
                                person_id=person,
                                option_id=option,
                                role="list_member",
                                list_position=member["position"],
                                ballot_name=member["name"],
                            )
                            if member["elected"]:
                                cs.add(
                                    "outcome",
                                    result_set_id=counted,
                                    option_id=option,
                                    candidacy_id=candidacy,
                                    kind="elected",
                                )
                day_singles = single_seats.filter(pl.col("day") == day)
                for region, collegio in (
                    day_singles.select("region", "collegio").unique().sort("region", "collegio").iter_rows()
                ):
                    key = fold(collegio)
                    rows = (
                        day_singles.filter((pl.col("region") == region) & (pl.col("collegio") == collegio))
                        .sort("votes", descending=True)
                        .to_dicts()
                    )
                    label = label_of(collegio)
                    area, unit = college(region, key, label, day)
                    contest_key = f"{day}:senate:{region}:{slug(key)}"
                    contest = cs.key("it.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="body_seats",
                        body_id=senate,
                        district_area_id=area,
                        magnitude=1,
                        electoral_system_id=systems[
                            "senato-valle-d-aosta" if region == "02" else "senato-trentino-2005"
                        ],
                        default_result_kind="counted",
                    )
                    name(contest, f"Senate, {label}")
                    cs.add("slug", path=f"{path}/senate/{slug(region_names[region])}/{slug(key)}", entity_id=contest)
                    counted = result_set(contest_key, election, contest)
                    for registered, voters, blank in (
                        day_accounts.filter((pl.col("region") == region) & (pl.col("place") == collegio))
                        .select("registered", "voters", "blank")
                        .iter_rows()
                    ):
                        if registered >= voters:
                            value_row(counted, unit, None, REGISTERED, registered)
                        value_row(counted, unit, None, VOTERS, voters)
                        value_row(counted, unit, None, BLANK, blank)
                    for position, row in enumerate(rows):
                        option = cs.key("it.option", f"{contest_key}:{slug(row['name'])}")
                        party = list_party(row["label"], day)
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=party,
                            ballot_label=row["label"],
                        )
                        person = person_of(
                            {
                                "senator": row["senator"],
                                "surname": row["name"],
                                "forename": "",
                                "birth": None,
                                "sex": None,
                                "key": key,
                            },
                            day,
                            region,
                        )
                        candidacy = cs.key("it.candidacy", f"{contest_key}:{slug(row['name'])}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=row["name"],
                        )
                        if party is not None:
                            cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party, kind="nominated")
                        value_row(counted, unit, option, VOTES, row["votes"])
                        if position == 0 and row["elected"]:
                            cs.add("outcome", result_set_id=counted, option_id=option, kind="elected")
                for region, label, senator in (
                    recovery.filter(pl.col("day") == day).select("region", "label", "senator").iter_rows()
                ):
                    contest_key = f"{day}:senate:{region}"
                    contest = cs.key("it.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="list_allocation",
                        body_id=senate,
                        district_area_id=region_areas[region],
                        magnitude=1,
                        electoral_system_id=systems["senato-trentino-recovery-2005"],
                        default_result_kind="counted",
                    )
                    name(contest, f"Senate, {region_names[region]} proportional seat")
                    cs.add("slug", path=f"{path}/senate/{slug(region_names[region])}", entity_id=contest)
                    counted = result_set(contest_key, election, contest, 0)
                    groups = day_singles.filter(pl.col("region") == region)
                    for group in sorted(groups["label"].unique()):
                        option = cs.key("it.option", f"{contest_key}:{slug(group)}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="list",
                            party_id=list_party(group, day),
                            ballot_label=group,
                        )
                        if group != label:
                            continue
                        cs.add("outcome", result_set_id=counted, option_id=option, kind="won", seats=1)
                        row = groups.filter(
                            (pl.col("label") == group) & pl.col("elected") & (pl.col("senator") == senator)
                        ).to_dicts()[0]
                        person = person_of(
                            {
                                "senator": senator,
                                "surname": row["name"],
                                "forename": "",
                                "birth": None,
                                "sex": None,
                                "key": fold(row["collegio"]),
                            },
                            day,
                            region,
                        )
                        candidacy = cs.key("it.candidacy", f"{contest_key}:{slug(group)}:{person}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person,
                            option_id=option,
                            role="list_member",
                            ballot_name=row["name"],
                        )
                        cs.add(
                            "outcome", result_set_id=counted, option_id=option, candidacy_id=candidacy, kind="elected"
                        )
                if day < ROSATO:
                    continue
                day_r_lists = rosato_lists.filter(pl.col("day") == day)
                day_r_members = rosato_members.filter(pl.col("day") == day)
                day_r_singles = rosato_singles.filter(pl.col("day") == day)
                day_r_accounts = rosato_accounts.filter(pl.col("day") == day)
                coalition_by_label = dict(day_r_lists.select("label", "coalition").unique("label").iter_rows())

                def accounted(result: UUID, unit: UUID, region: str, place: str | None, day_r_accounts=day_r_accounts):
                    for registered, voters, blank in (
                        day_r_accounts.filter(
                            (pl.col("region") == region)
                            & (pl.col("college").is_null() if place is None else pl.col("college") == place)
                        )
                        .select("registered", "voters", "blank")
                        .iter_rows()
                    ):
                        if registered >= voters:
                            value_row(result, unit, None, REGISTERED, registered)
                        value_row(result, unit, None, VOTERS, voters)
                        value_row(result, unit, None, BLANK, blank)

                def list_option(
                    contest_key: str,
                    contest: UUID,
                    label: str,
                    day=day,
                    election=election,
                    coalition_by_label=coalition_by_label,
                ) -> UUID:
                    option = cs.key("it.option", f"{contest_key}:{slug(label)}")
                    alliance = coalition_of(coalition_by_label.get(label), day, election)
                    party = list_party(label, day)
                    if alliance is not None and party is not None and (alliance, party) not in coalition_members:
                        coalition_members.add((alliance, party))
                        cs.add("alliance_member", alliance_id=alliance, party_id=party)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="list",
                        party_id=party,
                        alliance_id=alliance,
                        ballot_label=label,
                    )
                    return option

                plurals: dict[str, UUID] = {}
                for region in sorted(day_r_lists["region"].unique()):
                    region_lists = day_r_lists.filter(pl.col("region") == region)
                    rows = region_lists.filter(pl.col("college").is_null()).sort("votes", descending=True).to_dicts()
                    contest_key = f"{day}:senate:{region}"
                    root = cs.key("it.contest", contest_key)
                    extra, vacancy = VACANT.get((day, region), (0, None))
                    cs.add(
                        "contest",
                        id=root,
                        election_id=election,
                        kind="list_allocation",
                        body_id=senate,
                        district_area_id=region_areas[region],
                        magnitude=sum(row["seats"] for row in rows) + extra,
                        electoral_system_id=systems["senato-proportional-2017"],
                        default_result_kind="counted",
                    )
                    name(root, f"Senate, {region_names[region]}")
                    region_path = f"{path}/senate/{slug(region_names[region])}"
                    cs.add("slug", path=region_path, entity_id=root)
                    counted = result_set(contest_key, election, root)
                    for body in (SHARES_NOTE, vacancy, CAVEATS.get((day, region))):
                        if body is not None:
                            cs.add("note", entity_id=counted, kind="caveat", body_md=body)
                    region_unit = cs.unit("it.regione", region)
                    accounted(counted, region_unit, region, None)
                    for row in rows:
                        option = list_option(contest_key, root, row["label"])
                        value_row(counted, region_unit, option, VOTES, row["votes"])
                        if row["seats"]:
                            cs.add("outcome", result_set_id=counted, option_id=option, kind="won", seats=row["seats"])
                    for place in sorted(region_lists.filter(pl.col("college").is_not_null())["college"].unique()):
                        label = label_of(place)
                        area, unit = college(region, place, label, day, plural=True)
                        plurals[place] = area
                        child_key = f"{contest_key}:{slug(place)}"
                        child = cs.key("it.contest", child_key)
                        cs.add(
                            "contest",
                            id=child,
                            election_id=election,
                            kind="list_allocation",
                            body_id=senate,
                            district_area_id=area,
                            allocation_parent_id=root,
                            electoral_system_id=systems["senato-proportional-2017"],
                            default_result_kind="counted",
                        )
                        name(child, f"Senate, {label}")
                        cs.add("slug", path=f"{region_path}/{place.rsplit(' - ', 1)[1].lower()}", entity_id=child)
                        plural_set = result_set(child_key, election, child)
                        for body in (SHARES_NOTE, UNORDERED_NOTE if day < REDUCED else None):
                            if body is not None:
                                cs.add("note", entity_id=plural_set, kind="caveat", body_md=body)
                        accounted(plural_set, unit, region, place)
                        for row in (
                            region_lists.filter(pl.col("college") == place).sort("votes", descending=True).to_dicts()
                        ):
                            option = list_option(child_key, child, row["label"])
                            value_row(plural_set, unit, option, VOTES, row["votes"])
                            if row["seats"]:
                                cs.add(
                                    "outcome",
                                    result_set_id=plural_set,
                                    option_id=option,
                                    kind="won",
                                    seats=row["seats"],
                                )
                            for member in (
                                day_r_members.filter((pl.col("college") == place) & (pl.col("label") == row["label"]))
                                .sort("position", "surname", "forename", nulls_last=True)
                                .to_dicts()
                            ):
                                person = person_of(member | {"key": slug(row["label"])}, day, region)
                                candidacy = cs.key("it.candidacy", f"{child_key}:{slug(row['label'])}:{person}")
                                cs.add(
                                    "candidacy",
                                    id=candidacy,
                                    person_id=person,
                                    option_id=option,
                                    role="list_member",
                                    list_position=member["position"],
                                    ballot_name=f"{member['surname']} {member['forename']}",
                                )
                                if member["elected"]:
                                    cs.add(
                                        "outcome",
                                        result_set_id=plural_set,
                                        option_id=option,
                                        candidacy_id=candidacy,
                                        kind="elected",
                                    )
                for place in sorted(day_r_singles["college"].unique()):
                    rows = day_r_singles.filter(pl.col("college") == place).sort("votes", descending=True).to_dicts()
                    region, code = rows[0]["region"], place.split(" (")[0]
                    label = label_of(place)
                    area, unit = college(region, code, label, day, parent=plurals.get(rows[0]["parent"] or ""))
                    contest_key = f"{day}:senate:{region}:{slug(code)}"
                    contest = cs.key("it.contest", contest_key)
                    cs.add(
                        "contest",
                        id=contest,
                        election_id=election,
                        kind="body_seats",
                        body_id=senate,
                        district_area_id=area,
                        magnitude=1,
                        electoral_system_id=systems[
                            "senato-valle-d-aosta" if region == "02" else "senato-single-member-2017"
                        ],
                        default_result_kind="counted",
                    )
                    name(contest, f"Senate, {label}")
                    cs.add(
                        "slug",
                        path=f"{path}/senate/{slug(region_names[region])}/{code.rsplit(' - ', 1)[1].lower()}",
                        entity_id=contest,
                    )
                    counted = result_set(contest_key, election, contest)
                    if (day, f"{region}:{slug(code)}") in CAVEATS:
                        cs.add("note", entity_id=counted, kind="caveat", body_md=CAVEATS[day, f"{region}:{slug(code)}"])
                    accounted(counted, unit, region, place)
                    for row in rows:
                        ballot = f"{row['surname']} {row['forename']}"
                        joint = JOINT.get(row["lists"][0]) if len(row["lists"]) == 1 else None
                        lists = row["lists"] if joint is None else joint[1]
                        alliance = (
                            coalition_of(
                                joint[0] if joint is not None else coalition_by_label.get(lists[0]), day, election
                            )
                            if len(lists) > 1
                            else None
                        )
                        if joint is not None and alliance is not None:
                            for label in lists:
                                party = list_party(label, day)
                                if party is not None and (alliance, party) not in coalition_members:
                                    coalition_members.add((alliance, party))
                                    cs.add("alliance_member", alliance_id=alliance, party_id=party)
                        option = cs.key("it.option", f"{contest_key}:{slug(ballot)}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=list_party(lists[0], day) if len(lists) == 1 else None,
                            alliance_id=alliance,
                            ballot_label=ballot,
                        )
                        person = person_of(row | {"key": slug(code)}, day, region)
                        candidacy = cs.key("it.candidacy", f"{contest_key}:{slug(ballot)}")
                        cs.add(
                            "candidacy",
                            id=candidacy,
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=ballot,
                        )
                        for party in dict.fromkeys(list_party(label, day) for label in lists):
                            if party is not None:
                                cs.add("candidacy_nomination", candidacy_id=candidacy, party_id=party, kind="nominated")
                        value_row(counted, unit, option, VOTES, row["votes"])
                        if row["elected"]:
                            cs.add("outcome", result_set_id=counted, option_id=option, kind="elected")
                continue
            day_seats = seats.filter(pl.col("day") == day)
            day_declared = {
                r: (n, c)
                for r, n, c in declared.filter(pl.col("day") == day).select("region", "seats", "consistent").iter_rows()
            }
            day_candidates = candidates.filter(pl.col("day") == day)
            for region in sorted(day_candidates["region"].unique()):
                rows = day_candidates.filter(pl.col("region") == region).to_dicts()
                by_key: dict[str, list[dict]] = {}
                for row in rows:
                    by_key.setdefault(row["key"], []).append(row)
                if region == "02":
                    for key, group in by_key.items():
                        collegio_contest(day, election, path, region, key, group, None, "senato-valle-d-aosta")
                    continue
                magnitude, consistent = day_declared[region]
                eligendo = {
                    label: n
                    for label, n in day_seats.filter(pl.col("region") == region).select("label", "seats").iter_rows()
                    if n
                }
                extra = reserve(day, region)
                if day < MATTARELLA:
                    parent = cs.key("it.contest", f"{day}:senate:{region}")
                    placed = [
                        row
                        for key, group in sorted(by_key.items())
                        for row in collegio_contest(day, election, path, region, key, group, parent, "senato-1948")
                    ]
                    members = [row for row in placed if row["elected"]]
                    counts: dict[str, int] = {}
                    for row in members:
                        counts[row["label"]] = counts.get(row["label"], 0) + 1
                    deficit = [label for label, n in eligendo.items() if n > counts.get(label, 0)]
                    for row in extra:
                        if len(deficit) != 1:
                            raise ValueError(
                                f"{day} {region}: cannot place senator {row['senator']} in a group ({deficit})"
                            )
                        members.append(row | {"label": deficit[0], "person": person_of(row, day, region)})
                    counts = {}
                    for row in members:
                        counts[row["label"]] = counts.get(row["label"], 0) + 1
                    won = eligendo if consistent else counts
                    note = [GROUP_NOTE] if consistent else []
                    flagged = sum(1 for row in rows if row["flagged"])
                    if flagged != sum(counts.values()) or extra:
                        note.append(REPLACED_NOTE.format(flagged=flagged, declared=magnitude))
                    if extra:
                        note.append(
                            UNPLACED_NOTE.format(
                                label=extra and members[-1]["label"],
                                names=", ".join(
                                    f"{record[r['senator']]['forename']} {record[r['senator']]['surname']}"
                                    for r in extra
                                ),
                            )
                        )
                    if consistent and counts != eligendo:
                        differ = sorted(set(counts) | set(eligendo))
                        note.append(
                            "Eligendo and the archive differ on the group of some senators here: "
                            + "; ".join(
                                f"{label} {eligendo.get(label, 0)} seats, {counts.get(label, 0)} senators"
                                for label in differ
                                if eligendo.get(label, 0) != counts.get(label, 0)
                            )
                            + ". Seats follow Eligendo."
                        )
                    group_contest(
                        day,
                        election,
                        path,
                        region,
                        magnitude,
                        "senato-1948",
                        f"Senate, {region_names[region]}",
                        members,
                        won,
                        note,
                    )
                    continue
                placed = [
                    row
                    for key, group in sorted(by_key.items())
                    for row in collegio_contest(
                        day, election, path, region, key, group, None, "senato-single-member-1993"
                    )
                ]
                singles = sum(1 for row in placed if row["single"])
                if singles != len(by_key):
                    raise ValueError(f"{day} {region}: {singles} collegio winners in {len(by_key)} collegi")
                members = [row for row in placed if row["elected"] and not row["single"]]
                if magnitude - singles == 0:
                    continue
                counts = {}
                for row in members:
                    counts[row["label"]] = counts.get(row["label"], 0) + 1
                wins = {}
                for row in placed:
                    if row["single"]:
                        wins[row["label"]] = wins.get(row["label"], 0) + 1
                recovered = {
                    label: n - wins.get(label, 0) for label, n in eligendo.items() if n - wins.get(label, 0) > 0
                }
                note = (
                    []
                    if recovered == counts
                    else [
                        "Eligendo's seats less the collegio winners differ from the archive's recovered senators here; seats follow the archive."
                    ]
                )
                group_contest(
                    day,
                    election,
                    path,
                    region,
                    magnitude - singles,
                    "senato-recovery-1993",
                    f"Senate, {region_names[region]} proportional seats",
                    members,
                    counts,
                    note,
                )
        for row in cs.rows["contest"]:
            cs.add("note", entity_id=row["id"], kind="context", body_md=SENATE_NOTE)
        return cs.write()
