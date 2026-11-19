"""Serbia's presidential elections of 1990, 1992 and 1997, from the Republic Electoral Commission's archive.

The archive publishes these elections only as scans. For 1990 and 1992 it holds the Commission's report of the
national results, and for 1990 also the Republic Statistical Office's book, whose presidential tables are national
too. For 1997 it holds the Statistical Office's book of final results, which prints both presidential elections of
that year, each in two rounds, by municipality: votes for each candidate in table 4, and the polling boards,
registered voters, voters and invalid ballots in table 6. Every figure of the book was read from the page image,
checked against the printed percentages and against the totals the book prints for each constituency and the
Republic, and keeps its page and bounding box. The transcription lives in ``rik_predsednik.json`` beside this module.
"""

import json
import re
import subprocess
import urllib.parse
from dataclasses import dataclass
from datetime import date
from functools import cache
from pathlib import Path
from typing import Any
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.rs import rik

DATA_FILE = Path(__file__).with_name("rik_predsednik.json")
DIGITIZED = 8
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fSRB"))
VOTES, REGISTERED, CAST, VALID, INVALID, VOTERS = (
    rik.VOTES,
    rik.REGISTERED,
    rik.CAST,
    rik.VALID,
    rik.INVALID,
    rik.VOTERS,
)


def _url(path: str) -> str:
    return f"{rik.ARCHIVE}/predsednik/{urllib.parse.quote(path)}"


URLS = {
    "1990-list": _url("1990/1. - 1990 lista kandidata predsednik.pdf"),
    "1990-report": _url("1990/2. - 1990 izvestaj o ukupnim rezultatima pr.pdf"),
    "1990-book": _url("1990/3. - 1990 publikacija rzs.compressed.pdf"),
    "1992-list": _url("1992/1. - 1992 lista kandidata.pdf"),
    "1992-list-2": _url("1992/2. - 1992 dopuna liste kandidata.pdf"),
    "1992-list-3": _url("1992/3. - 1992 izmena i dopuna liste kandidata.pdf"),
    "1992-report": _url("1992/4. - 1992 izvestaj rezultati pr.pdf"),
    "1997-list": _url("1997 I/1. - 1997 I lista kandidata.pdf"),
    "1997-runoff": _url("1997 I/2. - 1997 I odluka o kandidatima na ponovljenom glasanju.pdf"),
    "1997-report": _url("1997 I/3. - 1997 (I) izvestaj o ukupnim rezultatima pr.pdf"),
    "1997-book": _url("1997 I/4. - 1997 I publikacija rzs.compressed.pdf"),
    "1997-ii-list": _url("1997 II/1. - 1997 II lista kandidata.pdf"),
    "1997-ii-runoff": _url("1997 II/2. - 1997 II odluka o ponavljanju glasanja za predsednika.pdf"),
    "1997-ii-report": _url("1997 II/3. - 1997 (II) izvestaj o ukupnim rezultatima pr.pdf"),
}

# Each municipality the 1997 book prints, and its code in the Statistical Office's register. The six Kosovo
# municipalities the Commission's register no longer lists (Glogovac, Kačanik, Štimlje, Dečani, Đakovica and Suva Reka)
# take their codes from the Office's register of settlements, and rik.OLDER gives them their okrug; Belgrade's
# municipalities are printed as "Београд-..." and Niš as one municipality, as they were then.
CODES = {
    "Београд-Врачар": "70114",
    "Београд-Палилула": "70203",
    "Београд-Савски Венац": "70220",
    "Београд-Стари Град": "70246",
    "Београд-Вождовац": "70106",
    "Београд-Гроцка": "70122",
    "Београд-Звездара": "70149",
    "Београд-Младеновац": "70173",
    "Београд-Земун": "70157",
    "Београд-Нови Београд": "70181",
    "Београд-Барајево": "70092",
    "Београд-Лазаревац": "70165",
    "Београд-Обреновац": "70190",
    "Београд-Раковица": "70211",
    "Београд-Сопот": "70238",
    "Београд-Чукарица": "70254",
    "Бачка Топола": "80071",
    "Мали Иђош": "80241",
    "Суботица": "80438",
    "Ада": "80012",
    "Бечеј": "80110",
    "Житиште": "80144",
    "Зрењанин": "80152",
    "Кањижа": "80195",
    "Кикинда": "80209",
    "Нова Црња": "80250",
    "Нови Бечеј": "80268",
    "Нови Кнежевац": "80276",
    "Сента": "80365",
    "Сечањ": "80373",
    "Чока": "80489",
    "Алибунар": "80039",
    "Бела Црква": "80098",
    "Вршац": "80128",
    "Ковачица": "80217",
    "Ковин": "80225",
    "Опово": "80292",
    "Панчево": "80314",
    "Пландиште": "80349",
    "Апатин": "80047",
    "Кула": "80233",
    "Оџаци": "80306",
    "Сомбор": "80381",
    "Бачки Петровац": "80080",
    "Беочин": "80101",
    "Нови Сад": "89010",
    "Сремски Карловци": "80411",
    "Бач": "80055",
    "Бачка Паланка": "80063",
    "Жабаљ": "80136",
    "Србобран": "80390",
    "Темерин": "80446",
    "Тител": "80454",
    "Врбас": "80462",
    "Инђија": "80179",
    "Ириг": "80187",
    "Пећинци": "80322",
    "Рума": "80357",
    "Сремска Митровица": "80403",
    "Стара Пазова": "80420",
    "Шид": "80497",
    "Ваљево": "70360",
    "Крупањ": "70661",
    "Лајковац": "70700",
    "Љиг": "70769",
    "Љубовија": "70777",
    "Мионица": "70831",
    "Осечина": "70882",
    "Уб": "71218",
    "Богатић": "70289",
    "Владимирци": "70408",
    "Коцељева": "70637",
    "Лозница": "70734",
    "Мали Зворник": "70793",
    "Шабац": "71269",
    "Велика Плана": "70386",
    "Смедерево": "71099",
    "Смедеревска Паланка": "71102",
    "Велико Градиште": "70394",
    "Голубац": "70475",
    "Жабари": "70521",
    "Жагубица": "70530",
    "Кучево": "70696",
    "Мало Црниће": "70807",
    "Петровац": "70912",
    "Пожаревац": "70947",
    "Аранђеловац": "70033",
    "Баточина": "70076",
    "Кнић": "70599",
    "Крагујевац": "70645",
    "Рача": "71013",
    "Топола": "71153",
    "Лапово": "71277",
    "Деспотовац": "70491",
    "Параћин": "70904",
    "Рековац": "71030",
    "Јагодина": "71048",
    "Свилајнац": "71056",
    "Ћуприја": "71200",
    "Бољевац": "70319",
    "Бор": "70327",
    "Зајечар": "70556",
    "Кладово": "70572",
    "Књажевац": "70602",
    "Мајданпек": "70785",
    "Неготин": "70840",
    "Сокобања": "71129",
    "Ариље": "70041",
    "Бајина Башта": "70068",
    "Косјерић": "70629",
    "Нова Варош": "70866",
    "Пожега": "70955",
    "Прибој": "70971",
    "Пријепоље": "70980",
    "Сјеница": "71072",
    "Ужице": "71145",
    "Чајетина": "71234",
    "Горњи Милановац": "70483",
    "Ивањица": "70564",
    "Лучани": "70742",
    "Чачак": "71242",
    "Врњачка Бања": "70459",
    "Краљево": "70653",
    "Нови Пазар": "70874",
    "Рашка": "71021",
    "Тутин": "71188",
    "Александровац": "70017",
    "Брус": "70343",
    "Варварин": "70378",
    "Крушевац": "70670",
    "Трстеник": "71170",
    "Ћићевац": "71196",
    "Бабушница": "70050",
    "Бела Паланка": "70084",
    "Гаџин Хан": "70467",
    "Димитровград": "70505",
    "Ниш": "70858",
    "Пирот": "70939",
    "Сврљиг": "71064",
    "Алексинац": "70025",
    "Блаце": "70262",
    "Дољевац": "70513",
    "Житорађа": "70548",
    "Куршумлија": "70688",
    "Мерошина": "70823",
    "Прокупље": "70998",
    "Ражањ": "71005",
    "Бојник": "70297",
    "Власотинце": "70424",
    "Лебане": "70718",
    "Лесковац": "70726",
    "Медвеђа": "70815",
    "Црна Трава": "71226",
    "Босилеград": "70335",
    "Бујановац": "70351",
    "Владичин Хан": "70416",
    "Врање": "70432",
    "Прешево": "70963",
    "Сурдулица": "71137",
    "Трговиште": "71161",
    "Витина": "90018",
    "Гњилане": "90042",
    "Косовска Каменица": "90140",
    "Ново Брдо": "90182",
    "Вучитрн": "90026",
    "Зубин Поток": "90093",
    "Лепосавић": "90158",
    "Подујево": "90247",
    "Србица": "90271",
    "Косовска Митровица": "90298",
    "Звечан": "90352",
    "Косово Поље": "90131",
    "Липљан": "90166",
    "Обилић": "90204",
    "Приштина": "90263",
    "Урошевац": "90301",
    "Штрпце": "90328",
    "Исток": "90107",
    "Клина": "90123",
    "Ораховац": "90212",
    "Пећ": "90239",
    "Призрен": "90255",
    "Гора": "90336",
    "Глоговац": "90034",
    "Качаник": "90115",
    "Штимље": "90310",
    "Дечани": "90069",
    "Ђаковица": "90085",
    "Сува Река": "90280",
}


@dataclass(frozen=True)
class Candidate:
    name: str  # as the list of candidates prints it, given name first
    nominator: str


@dataclass(frozen=True)
class Round:
    day: date
    round: int
    title: str
    native: str
    candidates: tuple[Candidate, ...]  # in ballot order
    files: tuple[str, ...]  # the files the package cites; the first is registered as an artifact here
    owned: tuple[str, ...]  # files whose artifact rows this package carries
    previous: date | None = None  # the first round, for a second round
    rerun_of: date | None = None  # the first round of a failed election this one repeats
    winner: int | None = None  # ballot position of the declared winner
    advanced: tuple[int, ...] = ()  # ballot positions of the candidates the Commission sent to the second round
    note: str = ""


GG = "Група грађана"
SPS = "Социјалистичка партија Србије"
SRS = "Српска радикална странка"
SPS_JUL_ND = "Социјалистичка партија Србије, Југословенска левица и Нова демократија"

ROUNDS = [
    Round(
        date(1990, 12, 9),
        1,
        "1990 Serbian presidential election",
        "Избори за председника Републике Србије 1990.",
        (
            Candidate("др Љубен-Ален Алексов", GG),
            Candidate("Никола Баровић", GG),
            Candidate("Мирослав Веселиновић", GG),
            Candidate("Ратимир Војводић", GG),
            Candidate("Предраг Вулетић", "Либерална странка"),
            Candidate("Миодраг-Миле Гојковић", "Странка самосталних привредника „Запис”"),
            Candidate("Саша Горанци", GG),
            Candidate("др Љубомир Грујић", GG),
            Candidate("Вук Драшковић", "Српски покрет обнове"),
            Candidate(
                "др Иван Ђурић",
                "Савез реформских снага Југославије и Удружење за југословенску демократску иницијативу",
            ),
            Candidate("Тихомир Живановић", GG),
            Candidate("Драган Јовановић", "Зелена странка"),
            Candidate("Јован Копривица", GG),
            Candidate("Томислав Крсмановић", "Покрет за заштиту људских права у Југославији и Група грађана"),
            Candidate("Милан Лазаревић", GG),
            Candidate("Слободан Милошевић", SPS),
            Candidate("Слободан Митић", "Савез свих Срба света"),
            Candidate("Милан Младеновић", GG),
            Candidate("Чедомир Нешић", GG),
            Candidate(
                "др Блажо Перовић",
                "Странка народне слоге, Странка Југословена, Југословенска социјалистичка демократска странка, "
                "Радничка партија Југославије, Демократска педагошка странка Југославије и Демократски реформски "
                "савез Муслимана Призрен (ЈУ блок)",
            ),
            Candidate("Милорад Радовић", GG),
            Candidate("Херцен Радоњић", GG),
            Candidate("Слободан Ранковић", GG),
            Candidate("Јован Стојковић", GG),
            Candidate("Миомир Тошић", GG),
            Candidate("Љиљана Ћуић", GG),
            Candidate("Сулејман Угљанин", "Странка демократске акције из Новог Пазара"),
            Candidate("Живан Хараван", "Странка социјалне правде"),
            Candidate("Велимир Цветић", "Социјалдемократска партија Југославије"),
            Candidate("Радивоје Шаранац", "Републиканска странка"),
            Candidate("Никола Шећероски", GG),
            Candidate("др Војислав Шешељ", GG),
        ),
        ("1990-report", "1990-book", "1990-list"),
        ("1990-report", "1990-book", "1990-list"),
        winner=16,
        note=(
            "The Commission's report and the Statistical Office's book give national figures only. The candidates' "
            "votes add up to 4,852,581, while both print 4,853,497 valid ballots; both are stored as printed. "
            "Ballots cast are the ballots used, which equal the valid and invalid ballots. Voting was annulled at 22 "
            "of 8,587 polling stations and not repeated."
        ),
    ),
    Round(
        date(1992, 12, 20),
        1,
        "1992 Serbian presidential election",
        "Избори за председника Републике Србије 1992.",
        (
            Candidate("Јездимир Васиљевић", GG),
            Candidate("Драган Васиљковић – Капетан Драган", "Група грађана симпатизера Капетана Драгана"),
            Candidate("Мирослав Милановић", GG),
            Candidate("Слободан Милошевић", SPS),
            Candidate("Милан Панић", GG),
            Candidate("Милан Парошки", "Народна странка и Српска опозиција"),
            Candidate("др Блажо Перовић", "Демократска отаџбинска коалиција"),
        ),
        ("1992-report", "1992-list", "1992-list-2", "1992-list-3"),
        ("1992-report", "1992-list", "1992-list-2", "1992-list-3"),
        winner=4,
        note=(
            "The Commission's report gives national figures only: registered voters, voters, the voters who voted "
            "for a candidate (stored as valid votes), and each candidate's votes. It prints no count of invalid "
            "ballots. Vuk Drašković withdrew and was struck from the list of candidates on 10 December 1992. Voting "
            "was annulled at 86 of 9,861 polling stations and not repeated."
        ),
    ),
    Round(
        date(1997, 9, 21),
        1,
        "1997 Serbian presidential election (September), first round",
        "Избори за председника Републике Србије 1997, први круг (21. септембар).",
        (
            Candidate("Милисав Банковић", "Радничка странка Југославије"),
            Candidate("Миодраг Видојковић", GG),
            Candidate("мр Предраг Вулетић", "Либерално-демократска странка"),
            Candidate("Вук Драшковић", "Српски покрет обнове"),
            Candidate("Ђорђе Дрљачић", GG),
            Candidate("Драган Ђорђевић", "Странка држављана Србије"),
            Candidate("Миодраг Миле Исаков", "Коалиција „Војводина”"),
            Candidate("Зоран Лилић", SPS_JUL_ND),
            Candidate("др Милан Младеновић", "Коалиција „Препород”"),
            Candidate("Вук Обрадовић", "Социјалдемократија"),
            Candidate("Милан Парошки", "Народна странка"),
            Candidate("Гвозден Сакић", GG),
            Candidate("Радомир Тукмановић", "Напредна странка"),
            Candidate("др Сулејман Угљанин", "Коалиција „Листа за Санџак др Сулејман Угљанин”"),
            Candidate("Бранко Чичић", "Партија природног закона"),
            Candidate("мр Небојша Човић", "Демократска алтернатива и Сељачка странка Србије"),
            Candidate("др Војислав Шешељ", SRS),
        ),
        ("1997-book", "1997-report", "1997-list"),
        ("1997-book", "1997-report", "1997-list"),
        advanced=(8, 17),
        note=(
            "No candidate won a majority of the voters, so the two leading candidates went to a second round on 5 "
            "October 1997. Voting was annulled at 20 of 9,827 polling stations and not repeated."
        ),
    ),
    Round(
        date(1997, 10, 5),
        2,
        "1997 Serbian presidential election (September), second round",
        "Избори за председника Републике Србије 1997, други круг (5. октобар).",
        (Candidate("Зоран Лилић", SPS_JUL_ND), Candidate("др Војислав Шешељ", SRS)),
        ("1997-book", "1997-report", "1997-runoff"),
        ("1997-runoff",),
        previous=date(1997, 9, 21),
        note=(
            "Fewer than half of the registered voters took part, so no candidate was elected although Vojislav Šešelj "
            "received the most votes, and the Commission ordered the whole election to be held again; it was "
            "repeated on 7 December 1997. Voting was annulled at 3 polling stations and not repeated."
        ),
    ),
    Round(
        date(1997, 12, 7),
        1,
        "1997 Serbian presidential election (December), first round",
        "Избори за председника Републике Србије 1997, први круг (7. децембар).",
        (
            Candidate("Миодраг Видојковић", GG),
            Candidate("мр Предраг М. Вулетић", "Либерално-демократска странка"),
            Candidate("Вук Драшковић", "Српски покрет обнове"),
            Candidate("Милан Милутиновић", SPS_JUL_ND),
            Candidate("др Драгољуб Мићуновић", "Демократски центар"),
            Candidate("др Вук Обрадовић", "Социјалдемократија"),
            Candidate("др Војислав Шешељ", SRS),
        ),
        ("1997-book", "1997-ii-report", "1997-ii-list"),
        ("1997-ii-report", "1997-ii-list"),
        rerun_of=date(1997, 9, 21),
        advanced=(4, 7),
        note=(
            "The repeated election, after the election of September and October 1997 failed. No candidate won a "
            "majority of the voters, so the two leading candidates went to a second round on 21 December 1997."
        ),
    ),
    Round(
        date(1997, 12, 21),
        2,
        "1997 Serbian presidential election (December), second round",
        "Избори за председника Републике Србије 1997, други круг (21. децембар).",
        (Candidate("Милан Милутиновић", SPS_JUL_ND), Candidate("др Војислав Шешељ", SRS)),
        ("1997-book", "1997-ii-report", "1997-ii-runoff"),
        ("1997-ii-runoff",),
        previous=date(1997, 12, 7),
        winner=1,
        note=(
            "The Statistical Office's book, processed before the Commission's final report, prints national totals "
            "slightly below the report's: 7,220,060 registered voters, 3,679,279 voters, 115,317 invalid ballots, "
            "2,177,462 votes for Milan Milutinović and 1,383,781 for Vojislav Šešelj, against the report's 7,225,860, "
            "3,683,714, 115,319, 2,181,808 and 1,383,868. The municipal figures are the book's and add up to its "
            "totals; the declared totals are the report's."
        ),
    ),
]

# National figures from the Commission's reports, and for 1990 the Statistical Office's book, which agree.
DECLARED: dict[date, tuple[dict[int, int], list[int]]] = {
    date(1990, 12, 9): (
        {REGISTERED: 7033610, VOTERS: 5029123, CAST: 5022958, VALID: 4853497, INVALID: 169461},
        [19123, 5355, 6180, 4414, 5019, 9262, 3409, 17675, 824674, 277398, 9892, 22458, 9677, 8095, 11034]
        + [3285799, 28978, 6459, 2553, 57420, 3425, 847, 2425, 1154, 904, 3764, 109459, 7791, 6575, 1918, 3168, 96277],
    ),
    date(1992, 12, 20): (
        {REGISTERED: 6949150, VOTERS: 4457671, VALID: 4377345},
        [61729, 87847, 28010, 2515047, 1516693, 147693, 20326],
    ),
    date(1997, 9, 21): (
        {REGISTERED: 7188544, VOTERS: 4131487, CAST: 4128889, VALID: 3973029, INVALID: 155860},
        [49158, 14105, 11463, 852808, 9430, 10684, 111166, 1474924, 10112]
        + [100523, 27100, 3293, 2647, 68446, 7097, 93133, 1126940],
    ),
    date(1997, 10, 5): (
        {REGISTERED: 7210557, VOTERS: 3531063, CAST: 3529436, VALID: 3425213, INVALID: 104223},
        [1691354, 1733859],
    ),
    date(1997, 12, 7): (
        {REGISTERED: 7226947, VOTERS: 3812010, CAST: 3810551, VALID: 3733640, INVALID: 76911},
        [29180, 21353, 587776, 1665822, 86583, 115850, 1227076],
    ),
    date(1997, 12, 21): (
        {REGISTERED: 7225860, VOTERS: 3683714, CAST: 3680995, VALID: 3565676, INVALID: 115319},
        [2181808, 1383868],
    ),
}

METHOD_BOOK = (
    "Digitized from the Republic Statistical Office's book of final results of the 1997 presidential elections, "
    "published in the Republic Electoral Commission's archive. Every figure was read from the page image, checked "
    "against the percentages the book prints beside it and against the totals it prints for each constituency and "
    "for the Republic, and keeps its page and bounding box. The book reports by municipality, grouped by the "
    "constituencies of the time; table 4 gives each candidate's votes, and table 6 the registered voters, the voters "
    "and the invalid ballots. Each constituency also has a row marked ПРС-13ДОП, a supplementary return of votes the "
    "constituency's commission counted outside its municipalities' polling stations; its votes count toward the "
    "constituency's totals and its registered voters do not and are not stored, and it is stored as an aggregate unit. Valid votes and "
    "ballots cast are printed for the Republic only. The voters include those who voted by letter, so the votes and "
    "invalid ballots add up to slightly fewer than the voters."
)
METHOD_REPORT = (
    "Transcribed from the Republic Electoral Commission's scanned report of the results, which gives national "
    "figures only. The figures are stored for the Republic as one aggregate unit."
)


@cache
def data() -> dict[str, Any]:
    return json.loads(DATA_FILE.read_text())


def slug(r: Round) -> str:
    return f"{r.day}-president"


def packages() -> list[str]:
    return [f"{rik.SOURCE}/{slug(r)}" for r in ROUNDS]


def fetch() -> None:
    for url in URLS.values():
        raw.fetch(rik.SOURCE, url, suffix=".pdf")


def parse() -> None:
    for r in ROUNDS:
        build(r).write()


def key(kind: str, *parts: object) -> str:
    return rik.key(kind, *parts)


def _sizes(path: Path) -> dict[int, tuple[float, float]]:
    out = subprocess.run(["pdfinfo", "-f", "1", "-l", "999", str(path)], capture_output=True, text=True, check=True)
    found = (re.match(r"Page\s+(\d+) size:\s+([\d.]+) x ([\d.]+)", line) for line in out.stdout.splitlines())
    return {int(m[1]): (float(m[2]), float(m[3])) for m in found if m}


def _pages(p: Package, name: str, used: set[int]) -> dict[int, str]:
    """Register the cited pages of a scanned file; boxes are in pixels of the page rendered at 100 dpi."""
    stored = raw.cached(rik.SOURCE, URLS[name])
    artifact = str(uuid5(package.NAMESPACE, f"source_artifact\x1f{rik.SOURCE_ID}\x1f{stored.storage_key}"))
    pages: dict[int, str] = {}
    for number, (width, height) in sorted(_sizes(stored.path).items()):
        if number not in used:
            continue
        pages[number] = p.id("page", stored.sha256, number)
        p.add(
            "source_page",
            id=pages[number],
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{rik.SOURCE}/{stored.sha256}-{number}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )
    return pages


def _person_name(label: str) -> str:
    return re.sub(r"^(др|мр)\s+", "", label.split(" – ")[0])


def build(r: Round) -> Package:
    s = slug(r)
    rows = data().get(str(r.day))
    depends = [f"{rik.SOURCE}/geo", f"{rik.SOURCE}/registry"]
    if r.previous:
        depends.append(f"{rik.SOURCE}/{r.previous}-president")
    if r.rerun_of:
        depends.append(f"{rik.SOURCE}/{r.rerun_of}-president")
    if rows is not None and "1997-book" not in r.owned:
        depends.append(f"{rik.SOURCE}/{date(1997, 9, 21)}-president")
    level = "by municipality" if rows is not None else "national totals"
    p = Package(
        f"{rik.SOURCE}/{s}",
        rik.SOURCE,
        f"The {r.title[:4]} {r.title[5:]}, {level}, digitized from the Republic Electoral Commission's archive",
        depends=list(dict.fromkeys(depends)),
    )
    p.external |= {rik.SERBIA, COUNTRY}
    for name in r.owned:
        p.artifact(raw.cached(rik.SOURCE, URLS[name]), rik.SOURCE_ID)
    for name in r.files:
        if name not in r.owned:
            p.uses(raw.cached(rik.SOURCE, URLS[name]))

    election = key("election", s)
    path = f"rs/{r.day}-presidential" + ("-runoff" if r.round > 1 else "")
    p.add(
        "election",
        id=election,
        polity_id=rik.SERBIA,
        kind="general" if r.round == 1 else "runoff",
        date_from=r.day,
        date_to=r.day,
        status="held",
        rerun_of_id=key("election", f"{r.rerun_of}-president") if r.rerun_of else None,
    )
    p.name(election, r.title, "en", "official")
    p.name(election, r.native, "sr", "native", False)
    p.add("slug", path=path, entity_id=election, is_current=True)
    contest = key("contest", s)
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="office",
        office_id=rik.PRESIDENCY,
        magnitude=1,
        round_number=r.round,
        previous_round_id=key("contest", f"{r.previous}-president") if r.previous else None,
        electoral_system_id=rik.SYSTEM_OF["president"],
        default_result_kind="counted",
    )
    p.name(contest, r.title, "en", "official")
    p.add("slug", path=f"{path}/president", entity_id=contest, is_current=True)
    result_set = key("result_set", s)
    p.add(
        "result_set",
        id=result_set,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=rik.SOURCE_ID,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    method = (METHOD_BOOK if rows is not None else METHOD_REPORT) + " " + r.note
    p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")

    options: dict[int, str] = {}
    for number, c in enumerate(r.candidates, 1):
        option = options[number] = key("option", s, number)
        p.add(
            "contest_option",
            id=option,
            contest_id=contest,
            kind="candidate",
            ballot_position=number,
            ballot_label=c.name,
        )
        person = key("person", s, number)
        p.add("person", id=person, birth_precision=None)
        p.name(person, _person_name(c.name), "sr", "official")
        candidacy = key("candidacy", s, number)
        p.add("candidacy", id=candidacy, person_id=person, option_id=option, role="head", ballot_name=c.name)
        p.add(
            "note",
            id=key("note", candidacy),
            entity_id=candidacy,
            kind="context",
            body_md=f"Предлагач: {c.nominator}",
            lang="sr",
        )
    if r.winner:
        p.add("outcome", result_set_id=result_set, option_id=options[r.winner], kind="won")
    for number in r.advanced:
        p.add("outcome", result_set_id=result_set, option_id=options[number], kind="advanced")

    declared, votes = DECLARED[r.day]
    assert len(votes) == len(r.candidates)
    for measure, value in declared.items():
        p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    for number, value in enumerate(votes, 1):
        p.add("declared", result_set_id=result_set, option_id=options[number], measure=VOTES, value=value)

    values: list[tuple[str, str | None, int, int]] = []
    locators: list[tuple[str, str | None, int, str, float, float, float, float]] = []
    if rows is None:
        unit = _unit(p, f"{r.day}:total", "aggregate", COUNTRY, "Република Србија")
        for measure, value in declared.items():
            values.append((unit, None, measure, value))
        for number, value in enumerate(votes, 1):
            values.append((unit, options[number], VOTES, value))
        difference = sum(votes) - declared[VALID]
        if difference:
            p.accept(
                "accounting.votes_valid",
                f"The candidates' votes in the Commission's report add up to {sum(votes):,}, {-difference:,} fewer "
                f"than the {declared[VALID]:,} valid ballots the report and the Statistical Office's book print; "
                "both are stored as printed.",
            )
    else:
        used = {int(box[0]) for row in rows for box in _boxes(row)}
        pages = _pages(p, "1997-book", used)
        national = rows[0]
        for row in rows:
            if row["kind"] in ("national", "constituency"):
                continue
            if row["kind"] == "supplement":
                unit = _unit(
                    p,
                    f"{r.day}:prs13dop:{rik._norm(row['constituency'])}",
                    "aggregate",
                    COUNTRY,
                    f"ПРС-13ДОП, изборна јединица {row['constituency']}",
                )
            else:
                code = CODES.get(row["name"])
                place = rik.area("rs.opstina", code) if code else COUNTRY
                unit = _unit(p, f"{r.day}:{code or '~' + rik._norm(row['name'])}", "standard", place, row["name"])
            figures = [(None, VOTERS, row["voters"])]
            if row["kind"] != "supplement":  # the book leaves these out of its constituency and national totals
                figures.append((None, REGISTERED, row["registered"]))
            figures.append((None, INVALID, row["invalid"]))
            figures += [(options[n], VOTES, cell) for n, cell in enumerate(row["votes"], 1)]
            for option, measure, (value, box) in figures:
                values.append((unit, option, measure, value))
                page, x0, y0, x1, y1 = box
                scale = 100 / 72
                locators.append((unit, option, measure, pages[page], x0 * scale, y0 * scale, x1 * scale, y1 * scale))
        reason = (
            "The book prints valid votes and ballots cast for the Republic only, so the declared valid votes and "
            "ballots cast have no municipal figures to add up."
        )
        if r.day == date(1997, 12, 21):
            printed = {REGISTERED: national["registered"][0], VOTERS: national["voters"][0]}
            printed[INVALID] = national["invalid"][0]
            names = {REGISTERED: "registered", VOTERS: "voters", INVALID: "invalid"}
            off = [f"{names[m]} {printed[m]:,} against {declared[m]:,}" for m in printed if printed[m] != declared[m]]
            off += [
                f"candidate {n} {cell[0]:,} against {votes[n - 1]:,}"
                for n, cell in enumerate(national["votes"], 1)
                if cell[0] != votes[n - 1]
            ]
            reason += (
                " The Statistical Office's book, whose municipal rows add up to its own national totals, was "
                "processed before the Commission's final report, whose national totals are declared (book against "
                "report): " + "; ".join(off)
            )
        p.accept("accounting.declared", reason)

    frame = pl.DataFrame(
        values,
        schema=["unit_id", "option_id", "measure", "value"],
        orient="row",
        schema_overrides={"option_id": pl.String},
    )
    p.frame(
        "result_value",
        frame.with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(DIGITIZED, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    if locators:
        p.frame(
            "value_locator",
            pl.DataFrame(
                locators,
                schema=["unit_id", "option_id", "measure", "page_id", "x0", "y0", "x1", "y1"],
                orient="row",
                schema_overrides={"option_id": pl.String},
            ).with_columns(
                result_set_id=pl.lit(result_set),
                channel=pl.lit(0, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                source_id=pl.lit(None, pl.String),
                measure=pl.col("measure").cast(pl.Int16),
            ),
        )
    return p


def _boxes(row: dict[str, Any]) -> list[list[float]]:
    return [row["registered"][1], row["voters"][1], row["invalid"][1], *(cell[1] for cell in row["votes"])]


def _unit(p: Package, code: str, kind: str, area: str, name: str) -> str:
    unit = key("unit", code)
    p.add(
        "reporting_unit",
        id=unit,
        polity_id=rik.SERBIA,
        scheme_code="rs.opstina",
        code=code,
        area_relation="within",
        area_id=area,
        kind=kind,
    )
    p.name(unit, name, "sr", "official")
    return unit
