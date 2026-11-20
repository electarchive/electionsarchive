"""Elections to the Sejm of the Polish People's Republic, 1947 to 1985, from the State Electoral Commission's notices of
the results in Monitor Polski.

Each notice is a scanned PDF with a rough OCR text layer. Every page was read twice by a vision model (reads a and b,
kept in the raw cache); `parse` reconciles the two reads with the text layer and with the figures the notices print
to check themselves: each constituency row's turnout and list percentages, each candidate's percent of the valid
votes, and the national totals of the prose. Figures the reads could not settle were read from the page images and
kept as constants below. One package per election day holds the constituencies' electorate, ballots, valid votes,
list votes (1952 to 1980), candidates' votes (1957 and later), and the deputies declared elected.
"""

import base64
import json
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections import Counter
from datetime import date
from decimal import Decimal
from difflib import SequenceMatcher
from functools import cache
from pathlib import Path
from typing import NamedTuple
from uuid import uuid5

import polars as pl
import pymupdf
from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "pl.pkw_sejm_prl"
PAGES = DATA / "pages" / SOURCE
MODEL = "claude-haiku-5-5"
PROMPT = "p1"
READS = ("a", "b")
ELI = "https://api.sejm.gov.pl/eli/acts/{act}/text.pdf"
NOTICES: dict[date, list[str]] = {
    date(1947, 1, 19): ["MP/1947/19", "MP/1947/31"],
    date(1952, 10, 26): ["MP/1952/1414"],
    date(1957, 1, 20): ["MP/1957/30"],
    date(1961, 4, 16): ["MP/1961/145"],
    date(1965, 5, 30): ["MP/1965/157"],
    date(1969, 6, 1): ["MP/1969/177"],
    date(1972, 3, 19): ["MP/1972/110"],
    date(1976, 3, 21): ["MP/1976/52"],
    date(1980, 3, 23): ["MP/1980/37"],
    date(1985, 10, 13): ["MP/1985/198"],
}


def _rows(page: int, text: str) -> dict[int, tuple[int, list[str]]]:
    out = {}
    for line in text.strip().splitlines():
        number, rest = line.split(" ", 1)
        cells = rest.split("|")
        out[int(number)] = (page, [number, *(cell.strip() for cell in cells)])
    return out


# Table rows read from the page images where both reads miss a row or agree on a misread figure; each row is
# checked against the percentages it prints and the column against the national total. A row whose printed
# percentage disagrees with its own counts (1952 row 16, 1961 row 58, 1969 row 70) keeps the counts as printed.
READINGS: dict[str, dict[int, tuple[int, list[str]]]] = {
    "MP/1952/1414": _rows(
        3,
        """
11 Piotrków|318.164|299.580|94,2|299.516|296.621|99,0
14 Inowrocław|235.826|226.062|95,9|226.043|225.829|99,9
15 Włocławek|196.154|191.665|97,7|191.609|191.131|99,8
16 Grudziądz|226.449|218.799|96,4|218.754|218.265|99,8
""",
    ),
    "MP/1957/30": _rows(
        4,
        """
2 Warszawa — Stare Miasto|242.956|231.855|95,43|231.685|230.446|99,46
3 Warszawa — Praga — Śródmieście|238.397|227.751|95,53|227.626|227.028|99,74
4 Łódź — Polesie|173.187|165.367|95,48|164.830|163.541|99,22
5 Łódź — Śródmieście|173.392|165.261|95,31|165.106|163.994|99,33
6 Łódź — Chojny|133.865|126.648|94,61|126.448|125.294|99,09
7 Bielsk Podlaski|183.421|176.706|96,34|175.695|170.523|97,06
8 Białystok|209.899|203.480|96,96|202.709|199.036|98,19
9 Łomża|161.542|152.968|94,69|152.480|148.632|97,48
10 Suwałki|105.512|98.175|93,05|97.648|95.882|98,19
11 Brodnica|107.139|103.669|96,76|103.107|100.009|96,99
12 Bydgoszcz|181.503|171.677|94,59|171.481|169.325|98,74
13 Grudziądz|134.201|128.871|96,03|128.253|125.989|98,23
14 Inowrocław|153.394|147.869|96,40|147.543|146.035|98,98
15 Toruń|165.427|157.672|95,31|157.354|155.595|98,88
16 Tuchola|120.316|115.207|95,75|115.072|113.402|98,55
17 Włocławek|129.936|122.225|94,06|121.996|119.997|98,36
18 Gdańsk|204.868|193.745|94,57|193.619|192.888|99,62
19 Gdynia|190.110|182.072|95,77|181.804|180.646|99,36
20 Elbląg|122.045|117.077|95,93|116.805|115.880|99,21
21 Tczew|188.233|180.391|95,83|179.703|175.348|97,58
22 Busko Zdrój|154.071|143.664|93,24|143.196|140.795|98,32
23 Jędrzejów|108.006|102.850|95,23|102.698|101.557|98,89
24 Kielce|189.507|173.928|91,78|173.641|172.335|99,25
25 Końskie|142.215|134.350|94,47|133.848|130.635|97,60
26 Ostrowiec Świętokrzyski|145.607|136.159|93,51|135.834|134.686|99,15
27 Radom|190.726|179.181|93,95|178.850|177.300|99,13
28 Starachowice|148.713|138.920|93,41|138.555|136.407|98,45
29 Koszalin|120.804|116.254|96,23|116.018|114.641|98,81
30 Słupsk|131.952|125.824|95,35|125.568|122.579|97,62
31 Szczecinek|141.270|136.964|96,95|136.598|135.143|98,93
32 Bochnia|120.136|111.522|92,83|111.197|109.348|98,34
33 Chrzanów|113.215|105.916|93,55|105.850|104.780|98,99
34 Kraków|332.957|306.429|92,03|306.254|305.592|99,78
35 Kraków|122.646|114.255|93,16|114.170|113.213|99,16
36 Myślenice|102.709|95.992|93,46|95.754|95.168|99,39
37 Nowy Sącz|100.541|94.558|94,05|94.532|93.907|99,34
""",
    )
    | _rows(5, "51 Pabianice|187.732|175.374|93,42|174.546|170.277|97,55")
    | _rows(6, "84 Gliwice|162.371|154.431|95,11|152.618|144.494|94,68"),
    "MP/1961/145": _rows(
        1,
        """
3 Warszawa — Praga|256.826|249.671|97,21|249.371|248.599|99,69
5 Łódź — Śródmieście|279.843|266.137|95,10|265.907|262.896|98,87
""",
    )
    | _rows(2, "8 Wrocław|284.252|269.732|94,89|269.482|265.872|98,66")
    | _rows(
        3,
        """
58 Kalisz|214.126|198.050|92,49|197.707|191.451|96,84
60 Ostrów Wielkopolski|240.960|228.459|94,81|228.090|223.394|97,94
""",
    ),
    "MP/1965/157": _rows(1, "6 Łódź — Śródmieście|295.717|286.833|97,00|286.463|284.021|99,15")
    | _rows(4, "80 Zielona Góra|299.655|289.941|96,76|289.884|287.649|99,23"),
    "MP/1969/177": _rows(4, "70 Otwock|296.820|293.361|99,83|293.314|289.247|98,61"),
    "MP/1985/198": _rows(
        2,
        """
10 Bydgoszcz|469.225|377.506|80,45|374.018
46 Kędzierzyn-Koźle|371.389|290.565|78,24|290.328
""",
    )
    | _rows(3, "50 Piotrków Trybunalski|443.417|352.856|79,58|352.273"),
}

# Candidates' votes read from the page images where the two reads agree on a figure that does not give the printed
# percent: (constituency, surname) to the votes printed. Those whose printed percent still disagrees with the votes
# and the constituency's valid votes are stored as printed.
VOTES_READ: dict[str, dict[tuple[int, str], int]] = {
    "MP/1957/30": {
        (33, "motyka"): 82221,
        (82, "kowalczyk"): 3581,
        (30, "galek"): 117180,
        (66, "stawinski"): 126323,
        (77, "kluza"): 157475,
        (78, "szczudlik"): 61087,
        (84, "galazka"): 135931,
    },
    "MP/1961/145": {
        (7, "kielczewski"): 256337,
        (18, "lasek"): 7828,
        (21, "olszewski"): 270973,
        (41, "maj"): 183407,
        (46, "olczak"): 171743,
        (47, "nowak"): 203810,
        (47, "warzecha"): 203843,
        (48, "nowakowski"): 186071,
        (48, "kusiak"): 185983,
        (51, "als"): 136833,
        (51, "wieczorkiewicz"): 4489,
        (52, "prufer"): 149345,
        (53, "pozny"): 182163,
        (60, "leczykiewicz"): 215813,
    },
    "MP/1969/177": {
        (29, "lasisz"): 176272,
        (44, "korczynski"): 272307,
        (44, "dabska"): 271754,
        (45, "gora"): 218567,
        (46, "ignar"): 233164,
        (46, "zak"): 233415,
        (49, "kuligowski"): 170491,
        (49, "pryma"): 169823,
        (58, "dabrowska"): 6938,
        (58, "wappa"): 5273,
    },
    "MP/1980/37": {(65, "plezia"): 384330},
}

# Page 3 of the 1947 notice (constituencies 29 to 46), which both reads missed, read from the page image: number,
# seat, valid votes, and the deputies elected. A letter the print broke (an "o" printed as "c") is given whole.
PAGE_1947 = """
29|Szczecin|153,615|Żymierski Michał; Zambrowski Roman; Grossfeld Ludwik; Krassowska Eugenia; Jaworska Helena; Wierusz-Kowalski Jan
30|Łobez|128,941|Przetacznik Eugeniusz; Kłosiewicz Wiktor; Żmijko Stefan; Kuzańska Maria; Lewińska Pelagia; Kosydarski Władysław
31|Poznań|205,030|Żymierski Michał; Leszczycki Stanisław; Bieńkowski Władysław; Banaczyk Władysław; Brzeziński Stefan
32|Leszno|282,825|Izydorczyk Jan; Wilanowski Stefan; Włodek Bronisław; Roch-Kowalski Franciszek; Piękniewski Zygmunt; Grajek Czesław; Nowak Tadeusz
33|Świebodzin|178,766|Widy-Wirski Feliks; Wolski Władysław; Kwiatkowski Stanisław; Swornowski Władysław; Strzałkowski Karol; Młotecki Władysław; Jaremczuk Stanisław; Gellert Piotr
34|Gniezno|214,569|Świerczewski Karol; Kłuszyńska Dorota; Piotrowski Antoni; Praga Rafał; Różański Józef; Mikołajczyk Stanisław
35|Kalisz|401,106|Baranowski Wincenty; Rumiński Bolesław; Dobrowolski Stanisław; Tupalski Marian; Jaszczukowa Maria; Nijak Franciszek; Kowalski Jan; Jonsik Wacław; Bońka Stanisław; Michejda Tadeusz; Karpała Stanisław
36|Wrocław I|122,737|Gomółka Władysław; Siemek Józef; Kulczyński Stanisław; Cieślak Stanisław; Mikulski Henryk; Rogala Witold
37|Wrocław II|227,286|Rzymowski Wincenty; Popławski Stanisław; Sokorski Włodzimierz; Ajnenkiel Eugeniusz; Sadrakuła Adam; Lityński Marian; Zajączkowski Zdzisław; Jaroszek Stefan; Stemplewski Stanisław; Ruszczycka Otylia
38|Legnica|221,499|Żymierski Michał; Hochfeld Julian; Drewnowski Jan-Jerzy; Mazur Franciszek; Pietrusiński Józef; Kuroczko Eustachy; Okulicki Wincenty; Oleszczak Witold; Orłowski Bolesław; Marzec Mieczysław; Lewandowicz Jan
39|Bolesławiec|159,557|Piaskowski Stanisław; Galiński Antoni; Bajkowski Antoni; Morawski Jerzy; Górny Alfred; Kowalik Władysław
40|Katowice|477,177|Gomółka Władysław; Kuryłowicz Adam; Ziętek Jerzy; Stachoń Roman; Nowak Roman; Jarosz Feliks; Jurek Jan; Dubiel Józef; Dąbrowski Jan; Sobol Wacław; Wojas Paweł; Labus Wiktor
41|Będzin|324,233|Cyrankiewicz Józef; Zawadzki Aleksander; Ochab Edward; Bień Aleksy; Szcześniak Józef; Polowa Maria; Popiel Mieczysław; Wanat Wacław; Cieślik Adolf
42|Bielsko|178,168|Kożusznik Bogusław; Juszkiewicz Aleksander; Nieszporek Ryszard; Stasiak Stanisław; Jacak Andrzej
43|Gliwice|267,814|Minc Hilary; Trąbalski Franciszek; Tkocz Karol; Szczyrek Jan; Szyćko Aleksander; Kuczewski Władysław; Nantka-Namirski Paweł; Nowacki Jerzy; Dąbrowski Stefan; Kieszczyński Józef; Wojciechowski Stanisław; Tomczyk Zofia
44|Koźle|178,645|Bożek Arkadiusz; Dietrych Tadeusz; Kowalski Aleksander; Gwis Alfons; Pawełczyk Roman; Kupczyński Bronisław; Kołodziejczyk Henryk; Kłos Bronisław
45|Opole|182,878|Zawadzki Aleksander; Rapacki Adam; Koj Jan; Czechowicz Eugeniusz; Affa Ludwik; Kisiel Marian; Tilgner Damazy-Jerzy; Kurkiewicz Władysław
46|Kraków|208,790|Drobner Bolesław; Krzyżanowski Adam; Nałkowska Zofia; Strzelecki Ryszard; Żuławski Zygmunt; Witaszek Jan; Bocheński Aleksander
"""

SYSTEM = (
    "You transcribe one scanned page of Monitor Polski, the Polish official gazette, holding part of the State "
    "Electoral Commission's notice of the results of an election to the Sejm (1947 to 1985). The notice has prose "
    "sections with national totals; a table of results by constituency (okręg wyborczy), one row per constituency "
    "with its number, seat, and figures such as persons entitled to vote, persons who voted, turnout percent, valid "
    "votes, votes for the lists of the Front, and a percent; a section listing, constituency by constituency, the "
    "votes for each candidate (a numbered name, the number of valid votes, and the percent of valid votes), and "
    "possibly the national list; and a section listing the deputies elected in each constituency (numbered names "
    "without votes). The 1947 notice instead has a table whose rows give the constituency, its valid votes, and the "
    "names of the deputies elected there. Transcribe exactly as printed, without correcting, summing, or guessing. "
    "Numbers keep their printed separators (dots, spaces, or commas). Keep Polish diacritics. A cell you cannot "
    "read is `?`. Give: `prose`, every prose paragraph that contains a number or names a section, verbatim, with "
    "section numerals (I, II, ...) as their own items; `table`, the constituency table on this page, with `columns`, "
    "its column headings in order, and `rows`, each with `cells` (every cell left to right, the constituency number "
    "first, a blank cell as an empty string), `names` (deputies printed in the row, if any, top to bottom), and "
    "`box`; `blocks`, every constituency block of candidate votes or elected names in reading order (columns "
    "left to right, top to bottom within each), each with `heading` (as printed, such as `Okręg Wyborczy nr 3 w "
    "Warszawie-Praga` or `Krajowa lista wyborcza`, or empty when the block continues from the previous page), "
    "`number` (the constituency number as printed, or empty), and `entries`, each with `position` (the printed "
    "number), `name` (as printed, surname first), `votes` and `percent` (as printed, empty when the block lists "
    "names only), and `box`. Every `box` is [x0, y0, x1, y1] as integers from 0 to 1000 across the image's width "
    "and height, covering the whole printed row or line. Under `other`, say in one line what else the page holds."
)
BOX = {"type": "array", "items": {"type": "integer"}}
TEXT = {"type": "string"}


def strict(properties: dict[str, object]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SCHEMA = strict(
    {
        "prose": {"type": "array", "items": TEXT},
        "table": strict(
            {
                "columns": {"type": "array", "items": TEXT},
                "rows": {
                    "type": "array",
                    "items": strict(
                        {
                            "cells": {"type": "array", "items": TEXT},
                            "names": {"type": "array", "items": TEXT},
                            "box": BOX,
                        }
                    ),
                },
            }
        ),
        "blocks": {
            "type": "array",
            "items": strict(
                {
                    "heading": TEXT,
                    "number": TEXT,
                    "entries": {
                        "type": "array",
                        "items": strict({"position": TEXT, "name": TEXT, "votes": TEXT, "percent": TEXT, "box": BOX}),
                    },
                }
            ),
        },
        "other": TEXT,
    }
)


def url(act: str) -> str:
    return ELI.format(act=act)


def fetch() -> None:
    for acts in NOTICES.values():
        for act in acts:
            raw.fetch(SOURCE, url(act), suffix=".pdf")
    read()


def pages(act: str) -> list[Path]:
    artifact = raw.cached(SOURCE, url(act))
    PAGES.mkdir(parents=True, exist_ok=True)
    found = sorted(PAGES.glob(f"{artifact.sha256}-*.png"))
    if not found:
        with tempfile.TemporaryDirectory() as scratch:
            subprocess.run(["pdftoppm", "-r", "150", "-gray", "-png", artifact.path, f"{scratch}/p"], check=True)
            for image in sorted(Path(scratch).glob("p-*.png")):
                shutil.move(image, PAGES / f"{artifact.sha256}-{int(image.stem.split('-')[1])}.png")
        found = sorted(PAGES.glob(f"{artifact.sha256}-*.png"))
    return sorted(found, key=lambda path: int(path.stem.split("-")[1]))


def key(act: str, page: Path, read: str) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}{read}/{act}/{page.stem.split('-')[1]}"


def message(image: Path, read: str) -> MessageCreateParamsNonStreaming:
    return MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=32000,
        system=SYSTEM,
        messages=[
            {
                "role": "user",
                "content": [
                    ImageBlockParam(
                        type="image",
                        source={
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(image.read_bytes()).decode(),
                        },
                    ),
                    TextBlockParam(type="text", text=f"Transcribe this page (read {read})."),
                ],
            }
        ],
        output_config={"effort": "medium", "format": {"type": "json_schema", "schema": SCHEMA}},
    )


def read() -> None:
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for acts in NOTICES.values():
        for act in acts:
            for page in pages(act):
                with Image.open(page) as image:
                    tokens = image.width * image.height / 750
                for one in READS:
                    wanted[key(act, page, one)] = (message(page, one), (tokens * price_in + 12000 * price_out) / 1e6)
    print(batch.ensure(SOURCE, "pages", wanted)[1])


def answer(act: str, page: Path, read: str) -> dict | None:
    try:
        message = json.loads(raw.cached(SOURCE, key(act, page, read)).path.read_text())
    except LookupError:
        return None
    return json.loads(next(block["text"] for block in message["content"] if block["type"] == "text"))


# Reconciling the reads
#
# Each page was read twice (a and b). The PDF also carries an OCR text layer, rough for names but mostly right for
# numbers, which serves as a third witness and places each number on the page. A table row is taken when its figures
# agree with the two percentages the row prints; a candidate's votes when they agree with the percent of the valid
# votes printed beside them; and every column, where the notice prints a national total, must add up to it.


class Word(NamedTuple):
    text: str
    box: tuple[float, float, float, float]


class Sheet(NamedTuple):
    act: str
    number: int
    image: Path
    reads: dict[str, dict]
    words: list[Word]


def digits(text: str | None) -> int | None:
    found = re.sub(r"\D", "", text or "")
    return int(found) if found else None


def percent(text: str | None) -> Decimal | None:
    found = re.search(r"\d+\s*[,.]\s*\d+", text or "")
    if found:
        return Decimal(re.sub(r"\s", "", found.group()).replace(",", "."))
    return Decimal(100) if re.fullmatch(r"\s*100\s*[,.]?\s*[—–-]*\s*%?\s*", text or "") else None


@cache
def sheets(act: str) -> list[Sheet]:
    document = pymupdf.open(raw.cached(SOURCE, url(act)).path)
    out = []
    for image in pages(act):
        number = int(image.stem.split("-")[1])
        reads = {one: found for one in READS if (found := answer(act, image, one)) is not None}
        page = document[number - 1]
        width, height = page.rect.width, page.rect.height
        words = [
            Word(text, (x0 / width, y0 / height, x1 / width, y1 / height))
            for x0, y0, x1, y1, text, *_ in page.get_text("words")
        ]
        out.append(Sheet(act, number, image, reads, words))
    return out


def fits(part: int, whole: int, printed: Decimal | None) -> bool:
    """Whether part/whole, as a percent, is the printed percent, rounded or cut to its places."""
    if printed is None or not whole:
        return False
    share = Decimal(part) * 100 / Decimal(whole)
    step = Decimal(1).scaleb(int(printed.as_tuple().exponent))
    return abs(share - printed) <= step * Decimal("0.55") or printed <= share < printed + step


COUNT = re.compile(r"^[^\w]{0,2}(?=(?:\D*\d){4})[\d.,'\s]+[^\w]{0,2}$")
SHARE = re.compile(r"^\D{0,2}\d{1,3}\s?,\s?\d{1,2}\D{0,3}$")
# The figures of a table row, from its last cells, by the shape of each notice's table.
FRONT = ("registered", "voters", "turnout", "valid", "front", "share")
TURNOUT = ("registered", "voters", "turnout", "valid")
VALID = ("valid",)
SHARES = {"turnout": ("voters", "registered"), "share": ("front", "valid")}
Box = tuple[float, float, float, float]


class Row(NamedTuple):
    number: int
    cells: dict[str, list[str]]
    figures: dict[str, int]
    settled: bool
    page: int
    boxes: dict[str, Box]
    witnesses: list[list[str]]


def lines(words: list[Word]) -> list[list[Word]]:
    out: list[list[Word]] = []
    for word in sorted(words, key=lambda w: (w.box[1] + w.box[3]) / 2):
        middle = (word.box[1] + word.box[3]) / 2
        if out and middle - (out[-1][0].box[1] + out[-1][0].box[3]) / 2 < 0.004:
            out[-1].append(word)
        else:
            out.append([word])
    return [sorted(line, key=lambda w: w.box[0]) for line in out]


def is_count(text: str) -> bool:
    return bool(COUNT.match(text)) and not SHARE.match(text)


def table_rows(sheet: Sheet, read: str) -> dict[int, list[str]]:
    """A read's table rows by number. A number out of sequence between two in sequence is a misprint for the one
    the sequence calls for, as with the second row 65 that 1976's table prints where row 56 belongs."""
    found = sheet.reads.get(read)
    listed: list[tuple[int | None, list[str]]] = []
    for row in found["table"]["rows"] if found else []:
        cells = [cell.strip() for cell in row["cells"]]
        number = re.search(r"\d+", cells[0]) if cells else None
        listed.append((int(number.group()) if number else None, cells))
    out: dict[int, list[str]] = {}
    for i, (number, cells) in enumerate(listed):
        before = listed[i - 1][0] if i else None
        after = listed[i + 1][0] if i + 1 < len(listed) else None
        if before is not None and after == before + 2 and number != before + 1:
            number = before + 1
        if number is not None and number not in out:
            out[number] = cells
    return out


def text_rows(sheet: Sheet, fields: tuple[str, ...]) -> dict[int, list[str]]:
    """Lines of the text layer that hold a whole table row, keyed by the row number printed at their left."""
    width = sum(1 for field in fields if field not in SHARES)
    out: dict[int, list[str]] = {}
    for line in lines(sheet.words):
        figures = [w.text for w in line if is_count(w.text)]
        shares = [w.text for w in line if SHARE.match(w.text)]
        first = line[0]
        if len(figures) == width and re.fullmatch(r"\d{1,3}\.?", first.text) and first.box[0] < 0.2:
            cells = iter(figures)
            rates = iter(shares)
            out.setdefault(
                int(first.text.rstrip(".")),
                [next(rates, "") if field in SHARES else next(cells) for field in fields],
            )
    return out


def _product(options: list[list[str]]) -> list[tuple[str, ...]]:
    out: list[tuple[str, ...]] = [()]
    for choices in options:
        out = [(*done, choice) for done in out for choice in choices]
    return out


def settle_row(
    fields: tuple[str, ...], weighted: list[tuple[int, list[str]]], support: set[int]
) -> tuple[dict[str, int], bool]:
    """The reading of a row whose counts give its printed percentages, preferring the values more witnesses give.

    The second value is False when no reading fits, or when two readings fit equally well."""
    witnesses = [witness for _, witness in weighted]
    options: list[list[str]] = []
    for i, _ in enumerate(fields):
        seen: list[str] = []
        for witness in witnesses:
            if i < len(witness) and witness[i] and witness[i] not in seen:
                seen.append(witness[i])
        options.append(seen or [""])
    scored: list[tuple[int, dict[str, int]]] = []
    for combo in _product(options):
        figures = {field: digits(cell) for field, cell in zip(fields, combo, strict=True) if field not in SHARES}
        if any(value is None for value in figures.values()):
            continue
        known = {field: value for field, value in figures.items() if value is not None}
        good = all(
            fits(known[SHARES[field][0]], known[SHARES[field][1]], percent(cell))
            for field, cell in zip(fields, combo, strict=True)
            if field in SHARES
        )
        order = [known[f] for f in ("front", "valid", "voters", "registered") if f in known]
        if not good or (order != sorted(order) and fields != TURNOUT):
            continue
        score = sum(
            sum(weight for weight, witness in weighted if i < len(witness) and digits(witness[i]) == known[field])
            + (known[field] in support)
            for i, field in enumerate(fields)
            if field in known
        )
        scored.append((score, known))
    if not scored:
        return {}, False
    scored.sort(key=lambda item: -item[0])
    tied = {tuple(figures.values()) for score, figures in scored if score == scored[0][0]}
    return scored[0][1], len(tied) == 1


def place(sheet: Sheet, value: int) -> Box | None:
    """The box of the text-layer word that prints a value, when exactly one does."""
    found = [w.box for w in sheet.words if is_count(w.text) and digits(w.text) == value]
    return found[0] if len(found) == 1 else None


def table(act: str, fields: tuple[str, ...]) -> dict[int, Row]:
    rows: dict[int, Row] = {}
    for sheet in sheets(act):
        read = {one: table_rows(sheet, one) for one in READS}
        text = text_rows(sheet, fields) if fields != VALID else {}
        numbers = set(read["a"]) | set(read["b"])
        looked = {number: cells for number, (page, cells) in READINGS.get(act, {}).items() if page == sheet.number}
        for number, cells in looked.items():
            counted = [cell for field, cell in zip(fields, cells[-len(fields) :], strict=True) if field not in SHARES]
            figures = dict(zip([f for f in fields if f not in SHARES], map(digits, counted), strict=True))
            boxes = {field: box for field, value in figures.items() if value and (box := place(sheet, value))}
            witnesses = [read[one][number][-len(fields) :] for one in READS if number in read[one]]
            figured = {field: value or 0 for field, value in figures.items()}
            rows[number] = Row(number, {"page": cells}, figured, True, sheet.number, boxes, witnesses)
        if not numbers:
            continue
        numbers |= {n for n in text if min(numbers) <= n <= max(numbers)}
        support = {value for w in sheet.words if is_count(w.text) and (value := digits(w.text)) is not None}
        for number in sorted(numbers - set(rows)):
            at = 1 if fields == VALID else -len(fields)
            witnesses = [(2, read[one][number][at:][: len(fields)]) for one in READS if number in read[one]]
            if number in text:
                witnesses.append((1, text[number]))
            figures, settled = settle_row(fields, witnesses, support)
            boxes = {field: box for field, value in figures.items() if (box := place(sheet, value)) is not None}
            cells = {one: read[one][number] for one in READS if number in read[one]}
            rows[number] = Row(number, cells, figures, settled, sheet.number, boxes, [w for _, w in witnesses])
    return rows


def balance(rows: dict[int, Row], fields: tuple[str, ...], printed: dict[str, int]) -> list[str]:
    """Settle a column that misses its printed total by one figure: when exactly one row has a witness whose value
    closes the gap and still gives the row's printed percentages, take it."""
    done = []
    for i, field in enumerate(fields):
        if field in SHARES or field not in printed:
            continue
        gap = printed[field] - sum(row.figures.get(field, 0) for row in rows.values())
        if not gap:
            continue
        found = set()
        for row in rows.values():
            if "page" in row.cells or field not in row.figures:
                continue
            for witness in row.witnesses:
                other = digits(witness[i]) if i < len(witness) else None
                if other is None or other - row.figures[field] != gap:
                    continue
                figures = row.figures | {field: other}
                checks = [
                    (figures[SHARES[share][0]], figures[SHARES[share][1]], [w[j] for w in row.witnesses if j < len(w)])
                    for j, share in enumerate(fields)
                    if share in SHARES and field in SHARES[share]
                ]
                if all(any(fits(part, whole, percent(cell)) for cell in cells) for part, whole, cells in checks):
                    found.add((row.number, other))
        if len(found) == 1:
            number, other = found.pop()
            rows[number] = rows[number]._replace(figures=rows[number].figures | {field: other})
            done.append(f"{field} of row {number} set to {other} by the printed total")
    return done


NUMBER = r"(\d{1,3}(?:[.\s]\d{3})+)"
# The national totals of the notice's prose, for the constituency ballot; 1985's national list prints its own.
TOTALS = {
    "registered": rf"uprawnionych do głosowania (?:we wszystkich okręgach wyborczych )?wynosiła\s*{NUMBER}",
    "voters": rf"(?:Oddano głosów|liczba oddanych głosów|okręgowych list wyborczych udział wzięło)\s*{NUMBER}",
    "valid": rf"Głosów ważnych oddano\s*{NUMBER}",
    "invalid": rf"głosów nieważnych (?:wynosi |wyniosła )?{NUMBER}",
    "front": rf"(?:Frontu Narodowego|Frontu Jedności Narodu) oddano\s*{NUMBER}",
}


def totals(act: str) -> dict[str, int]:
    """The national totals both reads give alike; a figure the reads disagree on is left out."""
    found: dict[str, dict[str, int]] = {}
    for one in READS:
        prose = " ".join(" ".join(s.reads[one]["prose"]) for s in sheets(act) if one in s.reads)
        prose = prose.split("na kandydatów z krajowej listy")[0]
        for field, pattern in TOTALS.items():
            match = re.search(pattern, prose, re.IGNORECASE)
            if match:
                found.setdefault(field, {})[one] = digits(match.group(1)) or 0
    return {field: values["a"] for field, values in found.items() if len(set(values.values())) == 1}


class Entry(NamedTuple):
    position: str
    name: str
    votes: int | None
    percent: Decimal | None
    page: int


class Block(NamedTuple):
    page: int
    heading: str
    number: int | None
    entries: list[Entry]
    votes: bool


VOTE_TEXT = re.compile(r"\d{1,3}(?:[.,\s]\d{3}(?!\d))+|\d+")


def figures(entry: dict) -> tuple[int | None, Decimal | None]:
    """A candidate's votes and percent, also where a read put both in the votes field ("329.162, czyli 98,48%")."""
    text = entry["votes"].strip()
    found = VOTE_TEXT.match(text)
    votes = digits(found.group()) if found else None
    share = percent(entry["percent"]) or (percent(text[found.end() :]) if found else None)
    return votes, share


def blocks(act: str, read: str) -> list[Block]:
    out = []
    for sheet in sheets(act):
        found = sheet.reads.get(read)
        for block in found["blocks"] if found else []:
            entries = [entry for entry in block["entries"] if entry["name"].strip()]
            if not entries:
                continue
            heading = block["heading"].strip()
            printed = re.search(r"\d+", block["number"]) or re.search(r"[Nn]r\.?\s*(\d+)", heading)
            number = int(printed.group(1) if printed.re.groups else printed.group()) if printed else None
            parsed = [Entry(e["position"].strip(), e["name"].strip(), *figures(e), sheet.number) for e in entries]
            votes = sum(1 for entry in parsed if entry.votes is not None) * 2 >= len(parsed)
            out.append(Block(sheet.number, heading, number, parsed, votes))
    return out


def normal(name: str) -> str:
    folded = unicodedata.normalize("NFKD", name.lower().replace("ł", "l"))
    return re.sub(r"[^a-z]+", " ", folded.encode("ascii", "ignore").decode()).strip()


def alike(one: str, other: str) -> float:
    return SequenceMatcher(None, normal(one), normal(other)).ratio()


def constituencies(listed: list[Block], valid: dict[int, int] | None = None) -> dict[int, list[Entry]]:
    """Blocks in reading order, each placed in the constituency whose valid votes give the percents its candidates
    print. A block that fits none, or several alike, follows its printed number, or continues the block before it
    when it prints none."""
    out: dict[int, list[Entry]] = {}
    current = 0
    for block in listed:
        fitted: dict[int, int] = {}
        for number, whole in (valid or {}).items():
            fitted[number] = sum(1 for e in block.entries if fits(e.votes or 0, whole, e.percent))
        best = max(fitted.values(), default=0)
        leaders = [number for number, score in fitted.items() if score == best]
        opens = bool(re.search(r"\d", block.heading)) or block.number == current + 1
        if best and len(leaders) == 1 and best * 2 > len(block.entries):
            current = leaders[0]
        elif best and current in leaders:
            pass
        elif opens:
            ahead = block.number is not None and current < block.number <= current + 12
            current = block.number if ahead or (not current and block.number) else current + 1
        if not current:
            continue
        out.setdefault(current, []).extend(block.entries)
    return out


class Candidate(NamedTuple):
    position: int
    name: str
    votes: int | None
    page: int
    settled: bool
    witnesses: list[Entry]


def pair(first: list[Entry], second: list[Entry]) -> list[tuple[Entry | None, Entry | None]]:
    """Align two reads of one constituency's entries, by order when they list as many, else by name."""
    if len(first) == len(second):
        return list(zip(first, second, strict=True))
    matcher = SequenceMatcher(None, [normal(e.name) for e in first], [normal(e.name) for e in second], autojunk=False)
    out: list[tuple[Entry | None, Entry | None]] = []
    i = j = 0
    for a, b, size in matcher.get_matching_blocks():
        left, right = first[i:a], second[j:b]
        for k in range(max(len(left), len(right))):
            one = left[k] if k < len(left) else None
            two = right[k] if k < len(right) else None
            if one and two and alike(one.name, two.name) < 0.6:
                out += [(one, None), (None, two)]
            else:
                out.append((one, two))
        out += list(zip(first[a : a + size], second[b : b + size], strict=True))
        i, j = a + size, b + size
    return out


def settle_votes(entries: list[Entry], valid: int | None, support: set[int]) -> tuple[int | None, bool]:
    """The votes of a candidate: a value that gives the printed percent of the valid votes, by agreement of reads
    and then by the text layer; unsettled when none fits or two fit alike."""
    values = [e.votes for e in entries if e.votes is not None]
    shares = [e.percent for e in entries if e.percent is not None]
    if not values:
        return None, False
    fitting = [v for v in dict.fromkeys(values) if valid and any(fits(v, valid, share) for share in shares)]
    if not fitting and valid:
        # Both reads misread one digit alike: take the text layer's figure that differs by that digit and fits.
        near = {
            other
            for value in values
            for other in support
            if len(str(other)) == len(str(value))
            and sum(x != y for x, y in zip(str(other), str(value), strict=True)) == 1
            and any(fits(other, valid, share) for share in shares)
        }
        if len(near) == 1:
            return near.pop(), True
    if not fitting:
        return values[0], False
    ranked = sorted(fitting, key=lambda v: (-values.count(v), -(v in support)))
    best = ranked[0]
    tied = [v for v in ranked if values.count(v) == values.count(best) and (v in support) == (best in support)]
    return best, len(tied) == 1


def candidates(act: str, valid: dict[int, int], national: bool = False) -> dict[int, list[Candidate]]:
    """Each constituency's candidates with their votes, from the vote blocks before the list of deputies elected.

    Blocks whose candidates hold millions of votes are 1985's national list, keyed 0."""
    support: dict[int, set[int]] = {
        sheet.number: {value for w in sheet.words if (value := digits(w.text)) is not None} for sheet in sheets(act)
    }
    read: dict[str, dict[int, list[Entry]]] = {}
    for one in READS:
        listed = [block for block in blocks(act, one) if block.votes]
        country = [b for b in listed if max((e.votes or 0) for e in b.entries) > 5_000_000]
        listed = [b for b in listed if b not in country]
        read[one] = {0: [e for b in country for e in b.entries]}
        read[one] |= constituencies(listed, valid) if not national else {}
    out: dict[int, list[Candidate]] = {}
    for number in sorted(set(read["a"]) | set(read["b"])):
        if national != (number == 0):
            continue
        whole = valid.get(number)
        found = []
        for index, (one, two) in enumerate(pair(read["a"].get(number, []), read["b"].get(number, []))):
            seen = [e for e in (one, two) if e is not None]
            votes, settled = settle_votes(seen, whole, support[seen[0].page])
            names = [e.name for e in seen]
            looked = VOTES_READ.get(act, {}).get((number, normal(names[0]).split(" ")[0]))
            if looked is not None:
                votes, settled = looked, True
            found.append(Candidate(index + 1, names[0], votes, seen[0].page, settled, seen))
        out[number] = found
    return out


class Elected(NamedTuple):
    name: str
    page: int
    printed: int | None


def elected_section(act: str) -> dict[str, list[Elected]]:
    """The names each read lists as deputies elected, from the first page whose name-only block is numbered 1."""
    out: dict[str, list[Elected]] = {}
    found = {one: [b for b in blocks(act, one) if not b.votes] for one in READS}
    starts = [b.page for listed in found.values() for b in listed if b.number == 1]
    if not starts:
        return {one: [] for one in READS}
    start = min(starts)
    for one, listed in found.items():
        current: int | None = None
        out[one] = []
        for block in listed:
            if block.page < start:
                continue
            if block.number is not None:
                current = block.number
            # 1985's national list: 50 names under no constituency's heading.
            if "kraj" in block.heading.lower() or (block.number is None and len(block.entries) >= 20):
                current = 0
            out[one] += [Elected(e.name, block.page, current) for e in block.entries]
    return out


def deputies(act: str) -> dict[int, list[Entry]]:
    """1952's deputies by constituency: each constituency lists its deputies, then its substitute deputies numbered
    from 1 again, so the deputies are the names before the numbering restarts. A read that does not show the restart
    is set aside for one that does."""
    runs: dict[str, dict[int, list[list[Entry]]]] = {}
    for one in READS:
        listed: dict[int, list[Entry]] = {}
        current = None
        for block in blocks(act, one):
            current = block.number if block.number is not None else current
            if current is not None:
                listed.setdefault(current, []).extend(block.entries)
        for number, entries in listed.items():
            split: list[list[Entry]] = []
            last = 0
            for entry in entries:
                if entry.name.endswith(":"):
                    if "zast" in entry.name.lower():
                        last = 10**6
                    continue
                position = digits(entry.position) or last + 1
                if not split or position <= last:
                    split.append([])
                split[-1].append(entry)
                last = position
            runs.setdefault(one, {})[number] = split
    out = {}
    for number in sorted(set(runs["a"]) | set(runs["b"])):
        shown = [runs[one][number] for one in READS if len(runs[one].get(number, [])) >= 2]
        if shown:
            out[number] = shown[0][0]
    return out


def surname(name: str) -> str:
    return normal(name).split(" ")[0]


def match(person: Elected, field: dict[int, list[Candidate]]) -> tuple[int, int] | None:
    """The candidate an elected name stands for: in its printed constituency, the one closest by name (or the only
    one with its surname); else, across all constituencies, a close and unambiguous name."""
    for scope, floor in (([person.printed] if person.printed in field else [], 0.75), (list(field), 0.95)):
        scored = sorted(
            ((alike(person.name, c.name), number, i) for number in scope for i, c in enumerate(field[number])),
            reverse=True,
        )
        if scored and scored[0][0] >= floor and (len(scored) == 1 or scored[1][0] < scored[0][0]):
            return scored[0][1], scored[0][2]
        if scope and floor < 0.85:
            same = [(number, i) for _, number, i in scored if surname(field[number][i].name) == surname(person.name)]
            if len(same) == 1:
                return same[0]
    return None


def winners(act: str, field: dict[int, list[Candidate]]) -> tuple[dict[int, set[int]], list[str]]:
    """Each constituency's elected candidates, as indexes into its candidates, matched by name to the section listing
    the deputies elected. A deputy both reads list under a constituency whose printed candidates lack them, as in
    1980's constituency 14, whose votes the notice prints for four candidates only, joins it without votes. Each
    candidate keeps the full name most of the reads give, across both sections."""
    chosen: dict[int, set[int]] = {}
    unmatched: dict[str, list[Elected]] = {}
    named: dict[tuple[int, int], list[str]] = {}
    for one, names in elected_section(act).items():
        for person in names:
            hit = match(person, field)
            if hit is None:
                unmatched.setdefault(one, []).append(person)
                continue
            chosen.setdefault(hit[0], set()).add(hit[1])
            named.setdefault(hit, []).append(person.name)
    left = []
    for person in unmatched.get("a", []):
        twin = [
            other
            for other in unmatched.get("b", [])
            if other.printed == person.printed and alike(other.name, person.name) >= 0.85
        ]
        if twin and person.printed in field:
            found = field[person.printed]
            found.append(Candidate(len(found) + 1, person.name, None, person.page, True, []))
            chosen.setdefault(person.printed, set()).add(len(found) - 1)
        else:
            left.append(f"{person.name} (printed {person.printed}, page {person.page})")
    for (number, i), names in named.items():
        candidate = field[number][i]
        votes = Counter([*(e.name for e in candidate.witnesses), *names])
        best = max(votes.values())
        if votes[candidate.name] < best:
            field[number][i] = candidate._replace(name=next(n for n, k in votes.items() if k == best))
    return chosen, left


# Building the packages

POLAND = str(uuid5(package.NAMESPACE, "polity\x1fPL"))
# Poland's country area and its national reporting unit, which production already holds.
TERRITORY = "f8fa3145-0d74-5c91-8464-7c0aa80ce95f"
NATION_UNIT = "273eed08-db52-57aa-adab-0cbdf622c0d3"
SCHEME = "pl.okreg_sejm_prl"
REGISTERED, CAST, VALID_VOTES, INVALID, VOTES, PREFERENCE = 2, 3, 4, 5, 1, 7
DIGITIZED = 8
WHOLE_PAGE: Box = (0.0, 0.0, 1.0, 1.0)
DAYS = sorted(NOTICES)
# The Sejm each election chose, its seats, and when its term ended (the next election).
SEATS = {
    date(1947, 1, 19): 444,
    date(1952, 10, 26): 425,
    date(1957, 1, 20): 459,
    date(1961, 4, 16): 460,
    date(1965, 5, 30): 460,
    date(1969, 6, 1): 460,
    date(1972, 3, 19): 460,
    date(1976, 3, 21): 460,
    date(1980, 3, 23): 460,
    date(1985, 10, 13): 460,
}
END = date(1989, 6, 4)
# Seats a constituency left unfilled: in 1957, constituency 37 (Nowy Sącz) elected 2 of its 3 deputies.
VACANT = {date(1957, 1, 20): {37: 1}}
FRONT_NAMES = {
    "fn": ("Front Narodowy", "National Front"),
    "fjn": ("Front Jedności Narodu", "Front of National Unity"),
}
METHOD = (
    "From the State Electoral Commission's notice of the results in Monitor Polski, a scanned PDF. Each page was "
    "read twice by a vision model and the two reads were reconciled with the PDF's OCR text layer and with the "
    "figures the notice prints to check itself: each constituency row's turnout and list percentages, each "
    "candidate's percent of the valid votes, and the national totals. Figures the reads could not settle were read "
    "from the page image. Every value keeps its page and, where the text layer places it, its box on the page."
)
LIST_METHOD = (
    " Voters received the list of the {front} and could strike candidates' names; a candidate's votes are the "
    "ballots that left the name unstruck, stored as preference votes within the list. The list's votes are the "
    "valid ballots cast for it, and the deputies are the candidates the notice declares elected."
)


class Seat(NamedTuple):
    number: int
    name: str
    figures: dict[str, int]
    row: Row | None
    candidates: list[Candidate]
    won: set[int]
    seats: int


def akey(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fields_of(day: date) -> tuple[str, ...]:
    return VALID if day.year == 1947 else TURNOUT if day.year == 1985 else FRONT


def seat_name(row: Row | None, fields: tuple[str, ...]) -> str:
    if row is None:
        return ""
    cells = row.cells.get("page") or row.cells.get("a") or row.cells.get("b") or []
    if fields == VALID:
        return re.sub(r"^\s*Okr\.?\s*\d+\.?\s*", "", cells[0] if cells else "").strip(" .")
    return cells[-len(fields) - 1].strip() if len(cells) > len(fields) else ""


def headings(act: str) -> dict[int, str]:
    """Constituency headings as printed ("Okręg Wyborczy Nr 66 w Jarosławiu"), for constituencies the table lacks."""
    out: dict[int, str] = {}
    for one in READS:
        for block in blocks(act, one):
            if block.number is not None and re.search(r"\d", block.heading):
                out.setdefault(block.number, re.sub(r"\s+", " ", block.heading).strip(" ."))
    return out


def rows_1947() -> dict[int, tuple[str, list[str]]]:
    """1947's constituencies from notice 19: the reads' table rows, and page 3 as read from the image."""
    found: dict[int, dict[str, list[str]]] = {}
    for sheet in sheets("MP/1947/19"):
        for one in READS:
            for row in sheet.reads[one]["table"]["rows"] if one in sheet.reads else []:
                number = re.search(r"\d+", row["cells"][0] if row["cells"] else "")
                if not number:
                    continue
                names = [n.strip() for n in row["names"] if n.strip()]
                if not names and len(row["cells"]) > 2:
                    names = [n.strip() for n in re.split(r"[;,]", row["cells"][2]) if n.strip()]
                found.setdefault(int(number.group()), {}).setdefault(one, names)
    out: dict[int, tuple[str, list[str]]] = {}
    for number, reads in found.items():
        # A read that runs a row into the next lists the row's names and then the next row's: take the shorter.
        listed = min(reads.values(), key=len)
        if any(other[: len(listed)] != listed for other in reads.values()):
            listed = reads.get("a") or listed
        out[number] = ("", listed)
    for line in PAGE_1947.strip().splitlines():
        number, seat, _, names = line.split("|")
        out[int(number)] = (seat, [n.strip() for n in names.split(";")])
    return out


def state_list_1947() -> list[str]:
    for one in READS:
        for block in blocks("MP/1947/19", one):
            if "PAŃSTW" in block.heading.upper() and len(block.entries) >= 70:
                return [e.name for e in block.entries]
    raise ValueError("the 1947 notice's state-list deputies were not found")


def election_seats(day: date) -> list[Seat]:
    acts = NOTICES[day]
    act = acts[0]
    fields = fields_of(day)
    rows = table(act, fields)
    printed = totals(act)
    balance(rows, fields, printed)
    vacant = VACANT.get(day, {})
    out: list[Seat] = []
    if day.year == 1947:
        listed = rows_1947()
        for number, (seat, names) in sorted(listed.items()):
            row = rows.get(number)
            if number in {int(line.split("|")[0]) for line in PAGE_1947.strip().splitlines()}:
                valid = digits(PAGE_1947.split(f"\n{number}|")[1].split("|")[1]) or 0
                box = place(sheets(act)[2], valid)
                row = Row(number, {}, {"valid": valid}, True, 3, {"valid": box} if box else {}, [])
            people = [Candidate(i + 1, name, None, row.page if row else 1, True, []) for i, name in enumerate(names)]
            name = seat or seat_name(row, fields)
            out.append(
                Seat(number, name, dict(row.figures) if row else {}, row, people, set(range(len(people))), len(people))
            )
        state = state_list_1947()
        people = [Candidate(i + 1, name, None, 4, True, []) for i, name in enumerate(state)]
        out.append(Seat(0, "", {}, None, people, set(range(len(people))), len(people)))
        return out
    printed_headings = headings(act)
    if day.year == 1952:
        for number, entries in sorted(deputies(act).items()):
            row = rows.get(number)
            people = [Candidate(i + 1, e.name.rstrip(","), None, e.page, True, []) for i, e in enumerate(entries)]
            name = seat_name(row, fields) or printed_headings.get(number, "")
            out.append(
                Seat(number, name, dict(row.figures) if row else {}, row, people, set(range(len(people))), len(people))
            )
        return out
    valid = {number: row.figures["valid"] for number, row in rows.items() if "valid" in row.figures}
    field = candidates(act, valid)
    if day.year == 1985:
        field |= candidates(act, {0: national_1985()["valid"]}, national=True)
    won, left = winners(act, field)
    if left:
        raise ValueError(f"{act}: deputies matched to no candidate: {left}")
    for number in sorted(field):
        row = rows.get(number)
        name = seat_name(row, fields) or printed_headings.get(number, "")
        figures = dict(row.figures) if row else {}
        chosen = won.get(number, set())
        out.append(Seat(number, name, figures, row, field[number], chosen, len(chosen) + vacant.get(number, 0)))
    return out


def national_1985() -> dict[str, int]:
    """The national list's own turnout, which the notice prints in section III."""
    found: dict[str, set[int]] = {}
    patterns = {
        "registered": rf"Liczba uprawnionych do głosowania wynosiła\s*{NUMBER}",
        "voters": rf"krajowej listy wyborczej wzięło udział\s*{NUMBER}",
        "valid": rf"Głosów ważnych oddano\s*{NUMBER}",
        "invalid": rf"głosów nieważnych\s*{NUMBER}",
    }
    for one in READS:
        prose = " ".join(" ".join(s.reads[one]["prose"]) for s in sheets("MP/1985/198") if one in s.reads)
        prose = prose.split("na kandydatów z krajowej listy", 1)[-1]
        for field, pattern in patterns.items():
            match = re.search(pattern, prose)
            if match:
                found.setdefault(field, set()).add(digits(match.group(1)) or 0)
    out = {field: values.pop() for field, values in found.items() if len(values) == 1}
    if set(out) != set(patterns):
        raise ValueError(f"the 1985 national list's turnout is not settled: {found}")
    return out


def check(day: date, seats: list[Seat]) -> list[str]:
    """Hold each election to what its notice prints, and return the differences left to report."""
    act = NOTICES[day][0]
    fields = fields_of(day)
    notes = []
    elected = sum(len(seat.won) for seat in seats)
    expected = SEATS[day] - sum(VACANT.get(day, {}).values())
    if elected != expected:
        raise ValueError(f"{day}: {elected} deputies declared elected, not {expected}")
    for seat in seats:
        if seat.number and day.year >= 1957 and seat.row is None:
            raise ValueError(f"{day}: constituency {seat.number} has no table row")
        for candidate in seat.candidates:
            if candidate.witnesses and not candidate.settled:
                raise ValueError(f"{day}: votes of {candidate.name} ({seat.number}) are not settled")
        if seat.row is not None and not seat.row.settled:
            raise ValueError(f"{day}: table row {seat.number} is not settled: {seat.row.witnesses}")
    printed = totals(act)
    for field in fields:
        if field in SHARES or field not in printed or day.year == 1952:
            continue
        summed = sum(seat.figures.get(field, 0) for seat in seats if seat.number)
        if summed != printed[field]:
            notes.append(f"{field}: constituencies add up to {summed:,}, the notice prints {printed[field]:,}")
    return notes


def source_of(p: Package, act: str) -> tuple[str, str]:
    year, position = act.split("/")[1:]
    source = p.id("source", act)
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher="Państwowa Komisja Wyborcza",
        title=f"Obwieszczenie Państwowej Komisji Wyborczej o wynikach wyborów do Sejmu (M.P. {year} poz. {position})",
        url=url(act),
        license="Official document: not subject to copyright under Article 4 of the Polish Copyright Act",
        attribution=f"Państwowa Komisja Wyborcza, Monitor Polski {year}, poz. {position}",
    )
    return source, p.artifact(raw.cached(SOURCE, url(act)), source)


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The constituencies of the Sejm elections of the Polish People's Republic, 1947 to 1985, one set per election",
    )
    p.external |= {POLAND, TERRITORY}
    p.add(
        "scheme",
        code=SCHEME,
        polity_id=POLAND,
        kind="electoral_district",
        rank=2,
        noun="Sejm constituency (1947–1985)",
    )
    for i, day in enumerate(DAYS):
        until = DAYS[i + 1] if i + 1 < len(DAYS) else END
        for seat in election_seats(day):
            if not seat.number:
                continue
            id = area(day, seat.number)
            p.add("area", id=id, scheme_code=SCHEME)
            p.add("area_code", area_id=id, code=code(day, seat.number), valid_from=day, valid_to=until)
            p.add("area_parent", area_id=id, parent_id=TERRITORY, valid_from=day, valid_to=until)
            label = f" ({seat.name})" if seat.name and not seat.name.lower().startswith("okręg") else ""
            polish = seat.name if seat.name.lower().startswith("okręg") else f"Okręg wyborczy nr {seat.number}{label}"
            p.name(id, f"{polish}, {day.year}", "pl", "official")
            p.name(id, f"Sejm constituency {seat.number}{label}, {day.year}", "en", "english", False)
    return p


def code(day: date, number: int) -> str:
    return f"{day.year}:{number:03d}"


def area(day: date, number: int) -> str:
    return akey("area", day, number)


BODY_PRL = akey("body", "sejm-prl")
BODY_1947 = akey("body", "sejm-ustawodawczy")


def front(code: str) -> str:
    return akey("party", code)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Legislative Sejm of 1947, the Sejm of the Polish People's Republic and its seats by term, and the fronts "
        "whose lists stood for it",
    )
    p.external.add(POLAND)
    p.add("body", id=BODY_1947, polity_id=POLAND, kind="constituent_assembly")
    p.name(BODY_1947, "Sejm Ustawodawczy", "pl", "official")
    p.name(BODY_1947, "Legislative Sejm", "en", "english", False)
    p.add("body", id=BODY_PRL, polity_id=POLAND, kind="unicameral")
    p.name(BODY_PRL, "Sejm Polskiej Rzeczypospolitej Ludowej", "pl", "official")
    p.name(BODY_PRL, "Sejm of the Polish People's Republic", "en", "english", False)
    for i, day in enumerate(DAYS):
        until = DAYS[i + 1] if i + 1 < len(DAYS) else END
        body = BODY_1947 if day.year == 1947 else BODY_PRL
        p.add("body_size", body_id=body, seats=SEATS[day], valid_from=day, valid_to=until)
    for code_, (polish, english) in FRONT_NAMES.items():
        id = front(code_)
        p.add("party", id=id, kind="bloc", polity_id=POLAND)
        p.name(id, polish, "pl", "official")
        p.name(id, english, "en", "english", False)
        p.name(id, code_.upper(), "pl", "abbreviation", False)
    return p


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{day}" for day in DAYS)]


def parse() -> None:
    geo().write()
    registry().write()
    for day in DAYS:
        election(day).write()


def emit() -> None:
    package.run("load", packages())


def election(day: date) -> Package:
    acts = NOTICES[day]
    seats = election_seats(day)
    differences = check(day, seats)
    fields = fields_of(day)
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day.year} Sejm election by constituency, digitized from the State Electoral Commission's notice in "
        "Monitor Polski",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {POLAND, TERRITORY}
    source, artifact = source_of(p, acts[0])
    pages: dict[int, str] = {}
    by_number = {sheet.number: sheet for sheet in sheets(acts[0])}

    def page(number: int) -> str:
        if number not in pages:
            sheet = by_number[number]
            with Image.open(sheet.image) as image:
                width, height = image.size
            pages[number] = p.id("page", acts[0], number)
            p.add(
                "source_page",
                id=pages[number],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{SOURCE}/{sheet.image.name}",
                width=width,
                height=height,
            )
        return pages[number]

    election_id = p.id("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=POLAND,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    if day.year == 1947:
        p.name(election_id, "1947 Polish legislative election", "en", "english")
        p.name(election_id, "Wybory do Sejmu Ustawodawczego 1947", "pl", "official", False)
    else:
        p.name(election_id, f"{day.year} Polish Sejm election", "en", "english")
        p.name(election_id, f"Wybory do Sejmu PRL {day.year}", "pl", "official", False)
    path = f"pl/{day}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    body = BODY_1947 if day.year == 1947 else BODY_PRL
    code_ = "fn" if day.year == 1952 else "fjn" if day.year < 1985 else None
    method = METHOD + (LIST_METHOD.format(front=FRONT_NAMES[code_][0]) if code_ and day.year >= 1957 else "")
    values: list[dict[str, object]] = []
    locators: list[dict[str, object]] = []

    def value(
        result_set: str, unit: str, option: str | None, measure: int, amount: int, at: int, box: Box | None
    ) -> None:
        keys = {"result_set_id": result_set, "unit_id": unit, "option_id": option, "measure": measure}
        values.append(keys | {"value": amount})
        x0, y0, x1, y1 = box or WHOLE_PAGE
        locators.append(
            keys
            | {
                "page_id": page(at),
                "x0": round(x0, 4),
                "y0": round(y0, 4),
                "x1": round(x1, 4),
                "y1": round(y1, 4),
            }
        )

    for seat in seats:
        national = seat.number == 0
        contest = p.id("contest", day, seat.number)
        label = f" ({seat.name})" if seat.name and not seat.name.lower().startswith("okręg") else ""
        if national:
            polish, english = (
                ("Lista państwowa", "State list") if day.year == 1947 else ("Krajowa lista wyborcza", "National list")
            )
        else:
            polish = seat.name if seat.name.lower().startswith("okręg") else f"Okręg wyborczy nr {seat.number}{label}"
            english = f"Constituency {seat.number}{label}"
        p.add(
            "contest",
            id=contest,
            election_id=election_id,
            kind="list_allocation" if day.year == 1947 else "body_seats",
            body_id=body,
            district_area_id=TERRITORY if national else area(day, seat.number),
            magnitude=seat.seats,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, polish, "pl", "official")
        p.name(contest, english, "en", "english", False)
        tail = ("lista-panstwowa" if day.year == 1947 else "krajowa-lista") if national else str(seat.number)
        p.add("slug", path=f"{path}/sejm/{tail}", entity_id=contest, is_current=True)
        result_set = p.id("result_set", day, seat.number)
        has_votes = bool(seat.figures) or any(c.votes is not None for c in seat.candidates)
        p.add(
            "result_set",
            id=result_set,
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0 if has_votes else 0.0,
        )
        p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        if national:
            unit = NATION_UNIT
            p.external.add(NATION_UNIT)
        else:
            unit = p.id("unit", day, seat.number)
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=POLAND,
                scheme_code=SCHEME,
                code=code(day, seat.number),
                area_relation="equals",
                area_id=area(day, seat.number),
                kind="standard",
                valid_from=day,
            )
            p.name(unit, polish, "pl", "official")
        # Turnout and the list's votes.
        if seat.row is not None:
            row = seat.row
            for field, measure in (("registered", REGISTERED), ("voters", CAST), ("valid", VALID_VOTES)):
                if field in row.figures and field in fields:
                    value(result_set, unit, None, measure, row.figures[field], row.page, row.boxes.get(field))
        if national and day.year == 1985:
            turnout = national_1985()
            for field, measure in (
                ("registered", REGISTERED),
                ("voters", CAST),
                ("valid", VALID_VOTES),
                ("invalid", INVALID),
            ):
                value(result_set, unit, None, measure, turnout[field], 24, None)
        # The list, its candidates, and the deputies elected.
        list_option = None
        if code_ is not None:
            list_option = p.id("option", day, seat.number, "list")
            p.add(
                "contest_option",
                id=list_option,
                contest_id=contest,
                kind="list",
                party_id=front(code_),
                ballot_position=1,
                ballot_label=FRONT_NAMES[code_][0],
            )
            if seat.row is not None and "front" in seat.row.figures:
                row = seat.row
                value(result_set, unit, list_option, VOTES, row.figures["front"], row.page, row.boxes.get("front"))
            p.add("outcome", result_set_id=result_set, option_id=list_option, kind="won", seats=len(seat.won))
        for index, candidate in enumerate(seat.candidates):
            tag = f"{day}:{seat.number}:{index + 1}"
            person = p.id("person", tag)
            p.add("person", id=person)
            p.name(person, candidate.name, "pl", "official")
            candidacy = p.id("candidacy", tag)
            option = p.id("option", tag)
            if list_option is not None:
                preference = option if candidate.votes is not None and day.year >= 1957 else None
                if preference is not None:
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        parent_option_id=list_option,
                        ballot_position=index + 1,
                        ballot_label=candidate.name,
                    )
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=list_option,
                    role="list_member",
                    list_position=index + 1,
                    ballot_name=candidate.name,
                    preference_option_id=preference,
                )
                owner = list_option
                measure = PREFERENCE
            else:
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=index + 1,
                    ballot_label=candidate.name,
                )
                p.add(
                    "candidacy",
                    id=candidacy,
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate.name,
                )
                owner = option
                measure = VOTES
            if candidate.votes is not None:
                sheet = by_number[candidate.page]
                value(result_set, unit, option, measure, candidate.votes, candidate.page, place(sheet, candidate.votes))
            if index in seat.won:
                # A deputy elected on a list is an outcome of the list's candidacy; a candidate who is the counted
                # option is an outcome of that option.
                member = candidacy if owner == list_option else None
                p.add("outcome", result_set_id=result_set, option_id=owner, candidacy_id=member, kind="elected")
        caveats = caveat(day, seat)
        if caveats:
            p.add(
                "note",
                id=p.id("note", result_set, "caveat"),
                entity_id=result_set,
                kind="caveat",
                body_md=caveats,
                lang="en",
            )
    for sheet in sheets(acts[0]):
        for one in READS:
            p.uses(raw.cached(SOURCE, key(acts[0], sheet.image, one)))
    frame = pl.DataFrame(values).with_columns(
        channel=pl.lit(0, pl.Int16),
        count_round=pl.lit(0, pl.Int16),
        flags=pl.lit(DIGITIZED, pl.Int16),
        measure=pl.col("measure").cast(pl.Int16),
    )
    p.frame("result_value", frame)
    p.frame(
        "value_locator",
        pl.DataFrame(locators).with_columns(
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            source_id=pl.lit(None, pl.String),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    if day.year == 1947:
        p.accept(
            "accounting.votes_valid",
            "the 1947 notice prints each constituency's valid votes and the deputies elected, but not the lists' votes",
        )
    if VACANT.get(day):
        p.accept(
            "outcomes.seats_magnitude",
            "in 1957 constituency 37 (Nowy Sącz) elected two deputies to its three seats, as the notice declares",
        )
    if day.year == 1957:
        p.accept(
            "accounting.preference_over_list",
            "in constituency 81 (Bytom) the notice prints one candidate's votes (177,589, 98.84% of the valid votes) "
            "above the votes for the Front's list (177,551, 98.82%); both agree with the percentages printed beside "
            "them and with the national totals, so they are stored as printed",
        )
    if differences:
        p.add(
            "note",
            id=p.id("note", election_id, "totals"),
            entity_id=election_id,
            kind="caveat",
            body_md="The constituency figures, as printed, do not add up to the national totals the notice prints: "
            + "; ".join(differences)
            + ".",
            lang="en",
        )
    return p


def caveat(day: date, seat: Seat) -> str:
    out = []
    if day.year == 1947 and seat.number:
        out.append(
            "The notice prints each constituency's valid votes and the deputies elected there, but not the votes of "
            "each list."
        )
    if day.year == 1947 and not seat.number:
        out.append("The notice names the deputies elected from the state lists but not their lists.")
    if day.year == 1952 and seat.row is None:
        out.append(
            "The digitized notice lacks the page of its table with constituencies 35 to 67 (Monitor Polski page "
            "1629), so this constituency carries its deputies only."
        )
    if VACANT.get(day, {}).get(seat.number):
        out.append(
            "Only two candidates won the absolute majority of valid votes the law required, so one of the "
            "constituency's three seats was left unfilled."
        )
    missing = [c.name for c in seat.candidates if c.votes is None and not c.witnesses]
    if day.year >= 1957 and missing:
        out.append(
            "The notice prints the votes of only some of this constituency's candidates; "
            + ", ".join(missing)
            + " are named among the deputies elected without their votes."
        )
    return " ".join(out)
