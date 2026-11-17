import base64
import io
import json
import re
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

from anthropic.types import ImageBlockParam, TextBlockParam
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "kr.ncu"
MODEL = "claude-haiku-5-5"
PROMPT = "p1"
READS = ("a", "b")
BASE = "https://library.nec.go.kr/neweps/epdata/EP24/EBM2024/01/"
VOLUMES = {
    2: ("EBM0120240244", "page-ca691188-43160278", [*range(430, 579), 581, 582]),
    3: ("EBM0120240245", "page-330dfd81-3fff65c1", [*range(516, 678), 679, 680]),
}

SYSTEM = (
    "You transcribe one scanned page of 대한민국선거사, the National Election Commission's history of Korean "
    "elections, from its chapter on the elections of delegates to the National Conference for Unification "
    "(통일주체국민회의 대의원선거) of 1972 or 1978. Most pages hold part of the list of delegates elected "
    "(當選者名簿): a table whose columns are 管轄選管委名 (the election commission, with its delegate count such as "
    "(10人)), 選擧區名 (the district, with its seat count such as (5)), 當選者姓名, 本籍, 住所, 職業, 性別, 生年月日, "
    "學歷, 經歷, and 得票數. The commission and district cells span the rows of all their delegates. Some pages "
    "hold later sections (再選擧, 當選人再決定, 一部再選擧) with their own small tables and dates, and the last "
    "page holds statistics tables by province. Transcribe exactly as printed, without correcting, summing, or "
    "guessing. Keep Hanja as Hanja and Hangul as Hangul; never convert one to the other. A cell you cannot read "
    "is `?`. Give: `sections`, one per delegate table on the page in reading order, each with `heading` (every "
    "heading printed above that table on this page, verbatim, joined with ` | `, such as `(가) 當選者名簿 | 서울特別市` "
    "or `나. 再選擧 | (1978年 9月 6日 施行)`, or empty when the table continues from the previous page) and `rows`, "
    "one per delegate, top to bottom, each with `commission` and `district` (the full text of that cell, its lines "
    "joined without spaces, such as `鍾路區(10人)` or `第1選擧區(5)` or `大靜邑(2)`, given only on the row where the "
    "cell begins on this page and empty on the rows below it that the same cell spans), `name` (without spaces), "
    "`origin` (本籍), `sex` (性別), `born` (生年月日 as printed, such as `1919. 4. 24`), and `votes` (得票數 as "
    "printed with its separators, or `無投票當選` when that is printed), where a ditto mark is `〃` and a blank cell "
    "is an empty string, and `box`, [x0, y0, x1, y1] as integers from 0 to 1000 across the image's width and "
    "height, covering the delegate's whole row. Under `tables`, transcribe every statistics table on the page, "
    "each with `title`, `columns` (its column headings in order, nested headings joined with ` / `), and `rows`, "
    "each with `cells` (every cell left to right, the row label first). Under `other`, say in one line what else "
    "the page holds."
)
BOX = {"type": "array", "items": {"type": "integer"}}
TEXT = {"type": "string"}


def strict(properties: dict[str, object]) -> dict[str, object]:
    return {"type": "object", "properties": properties, "required": list(properties), "additionalProperties": False}


SCHEMA = strict(
    {
        "sections": {
            "type": "array",
            "items": strict(
                {
                    "heading": TEXT,
                    "rows": {
                        "type": "array",
                        "items": strict(
                            {
                                "commission": TEXT,
                                "district": TEXT,
                                "name": TEXT,
                                "origin": TEXT,
                                "sex": TEXT,
                                "born": TEXT,
                                "votes": TEXT,
                                "box": BOX,
                            }
                        ),
                    },
                }
            ),
        },
        "tables": {
            "type": "array",
            "items": strict(
                {
                    "title": TEXT,
                    "columns": {"type": "array", "items": TEXT},
                    "rows": {"type": "array", "items": strict({"cells": {"type": "array", "items": TEXT}})},
                }
            ),
        },
        "other": TEXT,
    }
)


def url(volume: int, page: int) -> str:
    book, stem, _ = VOLUMES[volume]
    return f"{BASE}{book}/assets/page-images/{stem}-{page:04d}.jpg"


def project(volume: int) -> str:
    return f"{BASE}{VOLUMES[volume][0]}/project.json"


def pages() -> list[tuple[int, int]]:
    return [(volume, page) for volume, (_, _, numbers) in VOLUMES.items() for page in numbers]


def fetch() -> None:
    held = raw.keys(SOURCE)
    for wanted in [*(project(volume) for volume in VOLUMES), *(url(*page) for page in pages())]:
        if wanted not in held:
            raw.fetch(SOURCE, wanted, suffix=".json" if wanted.endswith(".json") else ".jpg")
            time.sleep(0.5)
    read()


def reply_key(volume: int, page: int, read: str) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}{read}/{volume}/{page}"


def message(volume: int, page: int, read: str) -> MessageCreateParamsNonStreaming:
    image = raw.cached(SOURCE, url(volume, page)).path
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
                            "media_type": "image/jpeg",
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
    for volume, page in pages():
        with Image.open(raw.cached(SOURCE, url(volume, page)).path) as image:
            tokens = image.width * image.height / 750
        for one in READS:
            wanted[reply_key(volume, page, one)] = (
                message(volume, page, one),
                (tokens * price_in + 12000 * price_out) / 1e6,
            )
    print(batch.ensure(SOURCE, "pages", wanted)[1])


def answer(volume: int, page: int, read: str) -> dict | None:
    try:
        reply = json.loads(raw.cached(SOURCE, reply_key(volume, page, read)).path.read_text())
    except LookupError:
        return None
    return json.loads(next(block["text"] for block in reply["content"] if block["type"] == "text"))


KOREA = str(uuid5(package.NAMESPACE, "polity\x1fKR"))
REGISTRY = f"{SOURCE}/registry"
DIGITIZED = 8
PUBLISHER = "중앙선거관리위원회 (National Election Commission)"
TITLES = {2: "대한민국선거사 제2집", 3: "대한민국선거사 제3집"}
GENERAL = {2: date(1972, 12, 15), 3: date(1978, 5, 18)}
DAYS = (date(1972, 12, 15), date(1978, 5, 18), date(1978, 9, 6), date(1979, 8, 1))
TERMS = {date(1972, 12, 15): "제1대", date(1978, 5, 18): "제2대"}
TABLES = {2: {578, 581, 582}, 3: {677, 679, 680}}
PROVINCES = (
    ("11", ("서울",), "서울특별시", "Seoul"),
    ("26", ("釜山", "부산"), "부산직할시", "Busan"),
    ("41", ("京畿", "경기"), "경기도", "Gyeonggi"),
    ("42", ("江原", "강원"), "강원도", "Gangwon"),
    ("43", ("忠淸北", "忠清北", "忠北", "충청북", "충북"), "충청북도", "North Chungcheong"),
    ("44", ("忠淸南", "忠清南", "忠濟南", "忠南", "충청남", "충남"), "충청남도", "South Chungcheong"),
    ("45", ("全羅北", "全北", "전라북", "전북"), "전라북도", "North Jeolla"),
    ("46", ("全羅南", "全南", "전라남", "전남"), "전라남도", "South Jeolla"),
    ("47", ("慶尙北", "慶尚北", "慶北", "경상북", "경북"), "경상북도", "North Gyeongsang"),
    ("48", ("慶尙南", "慶尚南", "慶南", "경상남", "경남"), "경상남도", "South Gyeongsang"),
    ("49", ("濟州", "제주"), "제주도", "Jeju"),
)
SCHEMES = (
    ("kr.ncu_sido", 1, "administrative", "province or special city"),
    ("kr.ncu_commission", 2, "administrative", "city, county, or district election commission"),
    ("kr.ncu_district", 3, "electoral_district", "delegate district"),
)
WHEN = re.compile(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*日")
UNOPPOSED = "無投票"
DITTO = "〃"
METHOD = (
    "Digitized from the list of elected delegates (當選者名簿) in the National Election Commission's 대한민국선거사. "
    "The book prints each delegate's votes but not the defeated candidates or each district's valid votes, so only "
    "the winners' votes are stored. A district marked 無投票當選 had no more candidates than seats and held no vote."
    "\n\nClaude Haiku 5.5 read each page twice, and the two readings were compared field by field. Pages where they "
    "differed were read again, up to six times, and each field took the value most readings agreed on. Where the "
    "readings of a commission, district, or name still split, an enlarged crop of that row was read three more "
    "times. Of the names and district names that stayed split, 29 names and 3 district names were settled by eye "
    "against the scan. Two names were blurred in one volume, so they were taken from the other volume's entry for "
    "the same delegate. Four names keep a □ for each character the scan does not show. Commission and district "
    "names that differ between the 1972 and 1978 volumes were checked against the scans, and misread characters "
    "were corrected. Each district's delegates were checked against its printed seat count, each commission's "
    "against its printed total, and each province's against the printed tables of seats and unopposed winners."
)
TURNOUT = (
    "The province totals of the statistics table 統一主體國民會議代議員選擧投票狀況表, digitized from the page. "
    "Registered voters are the table's 選擧人數, the electors of the districts that held a vote; its 總選擧人數 "
    "also counts the districts whose delegates were elected unopposed."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def source_id(volume: int) -> str:
    return key("source", volume)


def flat(text: str) -> str:
    return re.sub(r"\s+", "", text)


def province_of(text: str) -> str | None:
    found = {code for code, needles, _, _ in PROVINCES if any(needle in text for needle in needles)}
    if len(found) > 1:
        raise ValueError(f"heading names two provinces: {text}")
    return next(iter(found), None)


@dataclass
class Row:
    volume: int
    page: int
    box: list[int]
    heading: str
    commission: str
    district: str
    name: str
    sex: str
    born: str
    votes: str


VARIANTS = str.maketrans("清尚舉晋岘青", "淸尙擧晉峴靑")


def place(text: str, commission: bool) -> str:
    text = flat(text).translate(VARIANTS)
    if not re.search(r"[\u3400-\u9fff\uac00-\ud7af]", text):
        return ""
    if commission:
        text = re.sub(r"內區", "丙區", re.sub(r"([甲乙丙丁])(?=\(|$)", r"\1區", text)).replace("區區", "區")
        return re.sub(r"\((\d+)\)$", r"(\1人)", text)
    if found := re.fullmatch(r"第(\d+)[^()]*?(\((\d+)人?\))?", text):
        return f"第{found[1]}選擧區" + (f"({found[3]})" if found[3] else "")
    return re.sub(r"\((\d+)人\)$", r"(\1)", text)


def rows(reply: dict) -> list[Row]:
    out: list[Row] = []
    sex = ""
    votes = DITTO
    for section in reply["sections"]:
        for row in section["rows"]:
            text = flat(row["sex"])
            sex = "女" if "女" in text else "男" if "男" in text else sex
            cell = flat(row["votes"])
            if cell.startswith("無"):
                votes = UNOPPOSED
            elif cell != DITTO:
                votes = re.sub(r"[,.'’]", "", cell)
            out.append(
                Row(
                    0,
                    0,
                    row["box"],
                    flat(section["heading"]).translate(VARIANTS),
                    place(row["commission"], True) if flat(row["commission"]) else "",
                    place(row["district"], False) if flat(row["district"]) else "",
                    flat(row["name"]),
                    sex,
                    ".".join(re.findall(r"\d+", row["born"])),
                    votes,
                )
            )
    return out


def signature(heading: str) -> str:
    when = WHEN.search(heading)
    marks = [mark for mark in ("再決定", "再選擧", "當選者名簿") if mark in heading]
    return "|".join([province_of(heading) or "", when[0] if when else "", *marks]) if heading else ""


def shape(row: Row) -> tuple[str, ...]:
    return (signature(row.heading), row.commission, row.district, row.name, row.sex, row.born, row.votes)


CROPS = ("x", "y", "z")
CROP = (
    "You read one row cut from a scanned table in 대한민국선거사, the National Election Commission's list of "
    "delegates elected to the National Conference for Unification. Left to right, the image shows the cells "
    "管轄選管委名 (the election commission, such as 鍾路區(10人)), 選擧區名 (the district, such as 第1選擧區(5) or "
    "大靜邑(2)), and 當選者姓名 (the delegate's name, usually three Hanja), and perhaps the start of 本籍. "
    "Transcribe the three cells of the row in the middle of the image exactly as printed, Hanja as Hanja, without "
    "spaces; a cell that is blank in that row is an empty string. Rows above or below may show at the edges; ignore "
    "them. Look closely at each stroke: the names and places use rare characters."
)
CROP_SCHEMA = strict({"commission": TEXT, "district": TEXT, "name": TEXT})
REVIEWED: dict[tuple[int, int, int], tuple[str | None, str | None, str | None]] = {
    (2, 479, 6): (None, None, "金瑠鎔"),
    (2, 500, 16): (None, None, "金基爀"),
    (2, 524, 11): (None, None, "金鍾琯"),
    (2, 527, 12): (None, None, "金琫鎬"),
    (2, 560, 12): (None, None, "姜炳悳"),
    (2, 567, 15): (None, None, "徐徭士"),
    (3, 517, 6): (None, None, "兪炳彧"),
    (3, 535, 2): (None, None, "徐廷澔"),
    (3, 536, 4): (None, None, "姜信捌"),
    (3, 544, 12): (None, None, "鄭溢炅"),
    (3, 563, 11): (None, None, "劉載瑭"),
    (3, 572, 9): (None, None, "梁壘培"),
    (3, 574, 3): (None, None, "劉灝鍾"),
    (3, 583, 1): (None, None, "李濟源"),
    (3, 584, 1): (None, "乷味面(1)", None),
    (3, 591, 7): (None, None, "尹錫寶"),
    (3, 603, 1): (None, "龍進面(1)", None),
    (3, 613, 9): (None, None, "趙模九"),
    (3, 626, 14): (None, "唵川面(1)", None),
    (3, 631, 9): (None, None, "林善爀"),
    (3, 635, 12): (None, None, "金道熺"),
    (3, 642, 13): (None, None, "禹淳稷"),
    (3, 643, 1): (None, None, "金道瑩"),
    (3, 643, 15): (None, None, "朴鏞燮"),
    (3, 651, 5): (None, None, "金鍾濬"),
    (3, 658, 5): (None, None, "徐鎭瑚"),
    (3, 662, 3): (None, None, "趙鏞文"),
    (3, 663, 11): (None, None, "孫泰轍"),
    (3, 648, 5): (None, None, "朴龍乭"),
    (3, 562, 11): (None, None, "徐□□"),
    (3, 575, 3): (None, None, "全□祚"),
    (3, 575, 4): (None, None, "李□洛"),
    (2, 475, 14): (None, None, "南宮□"),
    (2, 532, 14): (None, None, "安琡"),
    (2, 546, 3): (None, None, "李熺守"),
    (3, 660, 11): (None, None, "鄭在脩"),
}
ELSEWHERE = {(2, 540, 3): (None, None, "任杜燦"), (3, 585, 12): (None, None, "趙昺翊")}
UNCERTAIN = (
    "The National Election Commission's scan prints this name in rare characters at low resolution. The machine "
    "readings disagreed, so the name was settled by eye against the scan. A character may be wrong."
)
ILLEGIBLE = (
    "The National Election Commission's scan does not show every character of this name legibly, so □ marks each "
    "character that could not be read."
)
BLANK = "대한민국선거사 prints no vote count for this delegate, so none is stored."
SMUDGED = "The vote count for this delegate is illegible in the National Election Commission's scan, so none is stored."
VOTELESS = {(2, 481, "李鎔國"): BLANK, (2, 514, "趙晙烈"): SMUDGED, (3, 666, "朴淳圭"): SMUDGED}
COMMISSIONS = {
    "蓮川郡": "漣川郡",
    "麗州郡": "驪州郡",
    "東草市": "束草市",
    "濟州郡": "溟州郡",
    "順川市": "順天市",
    "大邑市西區": "大邱市西區",
    "軍威軍": "軍威郡",
    "宣寧郡": "宜寧郡",
    "城東區": "城東區甲區",
    "西大門區川": "西大門區甲區",
    "道峰區中": "道峰區甲區",
    "釜山鎭區中": "釜山鎭區甲區",
}
DISTRICTS = {
    ("三陟郡", "盧谷面"): "蘆谷面",
    ("中原郡", "甑味面"): "乷味面",
    ("中原郡", "蘇臺面"): "蘇台面",
    ("丹陽郡", "大嶋面"): "大崗面",
    ("井邑郡", "?東面"): "瓮東面",
    ("保寧郡", "蝸山面"): "嵋山面",
    ("保寧郡", "帽山面"): "嵋山面",
    ("保寧郡", "鷺川面"): "鰲川面",
    ("光陽郡", "骨苔面"): "骨若面",
    ("公州郡", "儀當面"): "儀堂面",
    ("利川郡", "廳長面"): "麻長面",
    ("利川郡", "廐長面"): "麻長面",
    ("利川郡", "雪犀面"): "雪星面",
    ("南原郡", "二百面"): "二白面",
    ("南原郡", "周牛面"): "周生面",
    ("原城郡", "好椿面"): "好楮面",
    ("原城郡", "富諭面"): "富論面",
    ("原城郡", "所卓面"): "所草面",
    ("咸安郡", "伽郞面"): "伽倻面",
    ("咸平郡", "日也面"): "月也面",
    ("唐津郡", "大湖之面"): "大湖芝面",
    ("唐津郡", "大胡芝面"): "大湖芝面",
    ("唐津郡", "汐川面"): "沔川面",
    ("唐津郡", "順成面"): "順城面",
    ("善山郡", "玉坡面"): "玉城面",
    ("大德郡", "杞域面"): "杞城面",
    ("天原郡", "穩山面"): "稷山面",
    ("安城郡", "寶盖面"): "寶蓋面",
    ("安東郡", "古安面"): "吉安面",
    ("完州郡", "上闕面"): "上關面",
    ("密陽郡", "虎道面"): "淸道面",
    ("寶城郡", "得糧面"): "得粮面",
    ("寶城郡", "態峙面"): "熊峙面",
    ("寶城郡", "烏城面"): "鳥城面",
    ("寶城郡", "芦洞面"): "蘆洞面",
    ("尙州郡", "傘東面"): "牟東面",
    ("尙州郡", "傘西面"): "牟西面",
    ("尙州郡", "成昌面"): "咸昌面",
    ("居昌郡", "態陽面"): "熊陽面",
    ("平昌郡", "逢坪面"): "蓬坪面",
    ("平澤郡", "淸升面"): "浦升面",
    ("廣州郡", "寶村面"): "實村面",
    ("扶安郡", "蟬島面"): "蝟島面",
    ("抱川郡", "蘇乾面"): "蘇屹面",
    ("昌寧郡", "丈巖面"): "丈麻面",
    ("昌寧郡", "丈廠面"): "丈麻面",
    ("昌寧郡", "占谷面"): "吉谷面",
    ("昌寧郡", "蟾山面"): "靈山面",
    ("晉陽郡", "井杜面"): "井村面",
    ("楊州郡", "倉泉面"): "檜泉面",
    ("楊州郡", "漢金面"): "渼金面",
    ("楊州郡", "溪金面"): "渼金面",
    ("槐山郡", "佛項面"): "佛頂面",
    ("永同郡", "黃潤面"): "黃澗面",
    ("永川郡", "臨阜面"): "臨皐面",
    ("江華郡", "河帖面"): "河岾面",
    ("沃溝郡", "滄縣面"): "澮縣面",
    ("沃溝郡", "會縣面"): "澮縣面",
    ("沃溝郡", "卜山面"): "玉山面",
    ("河東郡", "王宗面"): "玉宗面",
    ("泗川郡", "扭洞面"): "杻洞面",
    ("珍島郡", "臨海面"): "臨淮面",
    ("珍島郡", "臨准面"): "臨淮面",
    ("益山郡", "八峯面"): "八峰面",
    ("聞慶郡", "甕城面"): "麻城面",
    ("聞慶郡", "龐城面"): "麻城面",
    ("莞島郡", "芦花面"): "蘆花面",
    ("莞島郡", "今日面"): "金日面",
    ("華城郡", "廳道面"): "麻道面",
    ("華城郡", "廠道面"): "麻道面",
    ("論山郡", "練武邑"): "鍊武邑",
    ("論山郡", "錬武邑"): "鍊武邑",
    ("達城郡", "坡西面"): "城西面",
    ("金堤郡", "白鷺面"): "白鷗面",
    ("金堤郡", "白鶴面"): "白鷗面",
    ("金堤郡", "連鳳面"): "進鳳面",
    ("金堤郡", "靑蛇面"): "靑蝦面",
    ("金海郡", "荼山面"): "菉山面",
    ("金海郡", "茶山面"): "菉山面",
    ("金海郡", "達永邑"): "進永邑",
    ("金海郡", "達禮面"): "進禮面",
    ("金陵郡", "禮侮面"): "禦侮面",
    ("長水郡", "蟾岩面"): "蟠岩面",
    ("長興郡", "有洽面"): "有治面",
    ("陝川郡", "大弁面"): "大幷面",
    ("陝川郡", "大井面"): "大幷面",
    ("陝川郡", "治炉面"): "冶爐面",
    ("陝川郡", "雙册面"): "雙冊面",
    ("陝川郡", "雙柘面"): "雙栢面",
    ("靈光郡", "歆良面"): "畝良面",
    ("靈光郡", "白岬面"): "白岫面",
    ("高敞郡", "富岩面"): "富安面",
    ("襄陽郡", "龔隆面"): "巽陽面",
    ("禮山郡", "挿橋面"): "揷橋面",
    ("江陵市", "江陵市區"): "江陵市選擧區",
    ("宜寧郡", "宣寧面"): "宜寧面",
}
MOVED = {(3, 673, 0): ("", None, None), (3, 673, 6): ("陝川郡(17人)", None, None)}
HEADINGS = {(3, 675): "濟州道"}
MISCOUNTED = {"1978-05-18:47:安東郡"}
UNBALANCED = (
    "The printed table lists Gyeonggi's invalid ballots as (+13)31,641 and does not explain the +13. As a result, the "
    "valid and invalid ballots add up to 13 more than the ballots cast. The figures are stored as printed."
)
UNOPPOSED_LISTED = {(3, "41"): 39, (3, "46"): 40}
MISPRINTED = {
    (2, 434, "第5選擧區"),
    (2, 463, "安養邑"),
    (2, 498, "靑南面"),
    (2, 503, "成歡面"),
    (2, 528, "縣山面"),
    (2, 529, "三鄕面"),
    (2, 547, "新寧面"),
    (2, 550, "船南面"),
}
MISPRINT = (
    "The book prints this district's seat count as ({printed}), but it lists {listed} delegates. The commission's "
    "printed total matches the list."
)


def crop_key(volume: int, page: int, index: int, read: str) -> str:
    return f"anthropic-batch:{MODEL}/{PROMPT}crop{read}/{volume}/{page}/{index}"


def crop_answer(volume: int, page: int, index: int, read: str) -> tuple[str, str, str] | None:
    try:
        reply = json.loads(raw.cached(SOURCE, crop_key(volume, page, index, read)).path.read_text())
    except LookupError:
        return None
    cells = json.loads(next(block["text"] for block in reply["content"] if block["type"] == "text"))
    return (
        place(cells["commission"], True) if flat(cells["commission"]) else "",
        place(cells["district"], False) if flat(cells["district"]) else "",
        flat(cells["name"]),
    )


def crop(volume: int, page: int, box: list[int]) -> bytes:
    with Image.open(raw.cached(SOURCE, url(volume, page)).path) as scan:
        top = max(0, box[1] * scan.height // 1000 - 12)
        bottom = min(scan.height, box[3] * scan.height // 1000 + 12)
        part = scan.crop((0, top, int(scan.width * 0.42), bottom)).convert("L")
        part = part.resize((part.width * 3, part.height * 3), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        part.save(out, format="PNG")
        return out.getvalue()


def crop_message(volume: int, page: int, box: list[int]) -> MessageCreateParamsNonStreaming:
    return MessageCreateParamsNonStreaming(
        model=MODEL,
        max_tokens=8000,
        system=CROP,
        messages=[
            {
                "role": "user",
                "content": [
                    ImageBlockParam(
                        type="image",
                        source={
                            "type": "base64",
                            "media_type": "image/png",
                            "data": base64.b64encode(crop(volume, page, box)).decode(),
                        },
                    ),
                    TextBlockParam(type="text", text="Transcribe the middle row's three cells."),
                ],
            }
        ],
        output_config={"effort": "high", "format": {"type": "json_schema", "schema": CROP_SCHEMA}},
    )


def majority(values: list[str]) -> str | None:
    ranked = Counter(values).most_common(2)
    if not ranked or ranked[0][1] < 2 or (len(ranked) > 1 and ranked[1][1] == ranked[0][1]):
        return None
    return ranked[0][0]


def resolve(volume: int, page: int) -> tuple[list[Row] | None, list[tuple[int, list[int]]]]:
    reads = [rows(reply) for one in "abcdef" if (reply := answer(volume, page, one)) is not None]
    length, support = Counter(len(found) for found in reads).most_common(1)[0]
    if support < 2:
        return None, []
    reads = [found for found in reads if len(found) == length]
    chosen: list[Row] = []
    disputed: list[tuple[int, list[int]]] = []
    stuck = False
    for index, lines in enumerate(zip(*reads, strict=True)):
        picked: list[str | None] = [
            majority(list(values)) for values in zip(*(shape(line) for line in lines), strict=True)
        ]
        if any(picked[field] is None for field in (1, 2, 3)):
            cropped = [found for read in CROPS if (found := crop_answer(volume, page, index, read)) is not None]
            for field in (1, 2, 3):
                if picked[field] is None:
                    picked[field] = majority([cells[field - 1] for cells in cropped])
            if any(picked[field] is None for field in (1, 2, 3)):
                disputed.append((index, [sorted(line.box[k] for line in lines)[len(lines) // 2] for k in range(4)]))
        for field, value in enumerate((REVIEWED | ELSEWHERE | MOVED).get((volume, page, index), (None, None, None)), 1):
            if value is not None:
                picked[field] = value
        if any(value is None for value in picked):
            stuck = True
            continue
        heading, commission, district, name, sex, born, votes = (value or "" for value in picked)
        heading = next(line.heading for line in lines if signature(line.heading) == heading)
        chosen.append(Row(volume, page, lines[0].box, heading, commission, district, name, sex, born, votes))
    return (None if stuck else chosen), disputed


def settle(volume: int, page: int) -> list[Row] | None:
    return resolve(volume, page)[0]


def unsettled() -> list[tuple[int, int]]:
    return [(volume, page) for volume, page in pages() if page not in TABLES[volume] and settle(volume, page) is None]


def reread() -> None:
    price_in, price_out = batch.PRICES[MODEL]
    wanted: dict[str, batch.Want] = {}
    for volume, page in pages():
        if page in TABLES[volume]:
            continue
        found, disputed = resolve(volume, page)
        if found is not None:
            continue
        if not disputed:
            done = sum(answer(volume, page, one) is not None for one in "abcdef")
            one = "abcdef"[done]
            with Image.open(raw.cached(SOURCE, url(volume, page)).path) as image:
                tokens = image.width * image.height / 750
            wanted[reply_key(volume, page, one)] = (
                message(volume, page, one),
                (tokens * price_in + 6000 * price_out) / 1e6,
            )
            continue
        for index, box in disputed:
            for read in CROPS:
                if crop_answer(volume, page, index, read) is None:
                    wanted[crop_key(volume, page, index, read)] = (
                        crop_message(volume, page, box),
                        (3000 * price_in + 4000 * price_out) / 1e6,
                    )
    print(f"{len(wanted)} requests to read again")
    if wanted:
        print(batch.ensure(SOURCE, "pages", wanted)[1])


@dataclass
class Delegate:
    row: Row
    born: date | None
    sex: str | None
    votes: int | None
    missing: str | None = None

    @property
    def unopposed(self) -> bool:
        return self.votes is None and self.missing is None


@dataclass
class District:
    election: date
    kind: str
    province: str
    commission: str
    name: str
    seats: int | None
    delegates: list[Delegate] = field(default_factory=list)
    revised: Delegate | None = None
    misprinted: int | None = None

    @property
    def ref(self) -> tuple[str, str, str]:
        return self.province, self.commission, self.name


def split(text: str, mark: str) -> tuple[str, int | None]:
    found = re.fullmatch(rf"(.*?)\((\d+){mark}\)", text)
    return (found[1], int(found[2])) if found else (text, None)


def birth(text: str) -> date | None:
    parts = [int(part) for part in text.split(".") if part]
    if len(parts) != 3 or not 1880 <= parts[0] <= 1950:
        return None
    try:
        return date(*parts)
    except ValueError:
        return None


def walk(volume: int) -> tuple[list[District], dict[str, int]]:
    districts: list[District] = []
    counts: dict[str, int] = {}
    election, kind = GENERAL[volume], "general"
    province: str | None = None
    commission: str | None = None
    current: District | None = None
    sex = votes = ""
    redetermined: list[tuple[str, Delegate]] = []
    last_heading = None
    for number in VOLUMES[volume][2]:
        if number in TABLES[volume]:
            continue
        found = settle(volume, number)
        if found is None:
            raise ValueError(f"vol. {volume} p. {number - 1}: the readings never agreed")
        for row in found:
            heading = HEADINGS.get((volume, number), row.heading)
            if heading and heading != last_heading:
                last_heading = heading
                if "再決定" in heading or "再選擧" in heading:
                    when = WHEN.search(heading)
                    if when is None:
                        raise ValueError(f"no date in {heading}")
                    election = date(int(when[1]), int(when[2]), int(when[3]))
                    kind = "redetermined" if "再決定" in heading else "by_election"
                    province = commission = None
                    current = None
                elif (code := province_of(heading)) is not None:
                    province = code
                elif "當選者名簿" not in heading and "第2代" not in heading:
                    raise ValueError(f"vol. {volume} p. {number - 1}: unknown heading {heading}")
            if row.commission:
                commission, count = split(row.commission, "人")
                commission = COMMISSIONS.get(commission, commission)
                if count is not None:
                    counts[f"{election}:{province}:{commission}"] = count
            if row.district and (row.commission or split(row.district, "") != (commission, None)):
                name, seats = split(row.district, "")
                name = DISTRICTS.get((commission or "", name), name)
                if commission is None:
                    raise ValueError(f"vol. {volume} p. {number - 1}: district {row.district} without a commission")
                current = District(election, kind, province or "", commission, name, seats)
                if (volume, number, name) in MISPRINTED:
                    current.seats, current.misprinted = None, seats
                districts.append(current)
            if current is None:
                raise ValueError(f"vol. {volume} p. {number - 1}: {row.name} outside a district")
            sex = row.sex or sex
            votes = votes if row.votes == DITTO else row.votes
            missing = VOTELESS.get((volume, number, row.name))
            if "?" in row.name or (missing is None and votes != UNOPPOSED and not votes.isdigit()):
                raise ValueError(f"vol. {volume} p. {number - 1}: unreadable row {row}")
            delegate = Delegate(
                row,
                birth(row.born),
                {"男": "male", "女": "female"}.get(sex),
                None if missing is not None or votes == UNOPPOSED else int(votes),
                missing,
            )
            if kind == "redetermined":
                redetermined.append((current.commission + current.name, delegate))
                districts.pop()
                continue
            current.delegates.append(delegate)
    home = {d.commission: d.province for d in districts if d.kind == "general"}
    for district in districts:
        if not district.province:
            district.province = home[district.commission]
    for where, delegate in redetermined:
        target = [d for d in districts if d.kind == "general" and d.commission + d.name == where]
        if len(target) != 1:
            raise ValueError(f"the redetermined district {where} matches {len(target)} districts")
        target[0].revised = delegate
    return districts, counts


def number(cell: str) -> int:
    digits = re.sub(r"\D", "", re.sub(r"\([^)]*\)", "", cell))
    return int(digits) if digits else 0


def table(volume: int, page: int, mark: str, columns: list[int]) -> dict[str, list[int]]:
    readings = []
    for one in READS:
        reply = answer(volume, page, one)
        if reply is None:
            raise ValueError(f"vol. {volume} p. {page - 1} has no reading {one}")
        chosen = [t for t in reply["tables"] if mark in flat(t["title"])]
        if len(chosen) != 1:
            raise ValueError(f"vol. {volume} p. {page - 1}: {len(chosen)} tables titled {mark}")
        out: dict[str, list[int]] = {}
        for line in chosen[0]["rows"]:
            label = flat(line["cells"][0]) if line["cells"] else ""
            code = "total" if "計" in label and province_of(label) is None else province_of(label)
            if code is not None and len(line["cells"]) > max(columns):
                out[code] = [number(line["cells"][column]) for column in columns]
        readings.append(out)
    if readings[0] != readings[1] or len(readings[0]) != len(PROVINCES) + 1:
        raise ValueError(f"vol. {volume} p. {page - 1}: the readings of {mark} differ or miss a province")
    return readings[0]


def check(volume: int, districts: list[District], counts: dict[str, int]) -> dict[str, list[int]]:
    general = [d for d in districts if d.kind == "general"]
    problems: list[str] = []
    for d in districts:
        if d.misprinted == len(d.delegates):
            problems.append(f"{d.election} {d.ref}: listed as misprinted but matches its seat count")
        if d.seats is not None and len(d.delegates) != d.seats:
            problems.append(f"{d.election} {d.ref}: {len(d.delegates)} delegates for {d.seats} seats")
        if len({x.unopposed for x in d.delegates}) > 1:
            problems.append(f"{d.election} {d.ref}: some delegates unopposed and some not")
    held: Counter[str] = Counter()
    for d in general:
        held[f"{d.election}:{d.province}:{d.commission}"] += len(d.delegates)
    for where, count in counts.items():
        if where in held and (held[where] != count) != (where in MISCOUNTED):
            problems.append(f"{where}: {held[where]} delegates, {count} printed")
    first = TABLES[volume]
    turnout = table(volume, min(first), "投票狀況", [1, 5, 8, 6, 7])
    seats = table(volume, sorted(first)[1], "得票數別", [1, 2])
    for code, *_ in PROVINCES:
        mine = [d for d in general if d.province == code]
        filled = sum(len(d.delegates) for d in mine)
        unopposed = sum(len(d.delegates) for d in mine if d.delegates and d.delegates[0].unopposed)
        if [filled, unopposed] != [seats[code][0], UNOPPOSED_LISTED.get((volume, code), seats[code][1])]:
            problems.append(f"{code}: {filled} delegates and {unopposed} unopposed, printed {seats[code]}")
        if len(mine) != turnout[code][0]:
            problems.append(f"{code}: {len(mine)} districts, printed {turnout[code][0]}")
    for column in range(5):
        if sum(turnout[code][column] for code, *_ in PROVINCES) != turnout["total"][column]:
            problems.append(f"turnout column {column}: provinces do not sum to the printed total")
    if problems:
        raise ValueError("\n".join(problems))
    return turnout


def person(delegate: Delegate) -> str:
    if delegate.born is not None:
        return key("person", delegate.row.name, delegate.born.isoformat())
    return key("person", delegate.row.volume, delegate.row.page, delegate.row.name)


def province_names(code: str) -> tuple[str, str]:
    return next((korean, english) for found, _, korean, english in PROVINCES if found == code)


def cite(p: Package, done: set[tuple[int, int]], volume: int, page: int) -> str:
    page_key = key("page", volume, page)
    if (volume, page) in done:
        return page_key
    done.add((volume, page))
    artifact = raw.cached(SOURCE, url(volume, page))
    target = DATA / "pages" / SOURCE / f"{artifact.sha256}.png"
    with Image.open(artifact.path) as scan:
        width, height = scan.size
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            scan.convert("L").save(target, optimize=True)
    p.add(
        "source_page",
        id=page_key,
        artifact_id=p.artifact(artifact, source_id(volume)),
        page_number=page - 1,
        image_key=f"pages/{SOURCE}/{target.name}",
        width=width,
        height=height,
    )
    return page_key


def registry(districts: list[District]) -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The National Conference for Unification, its delegate districts' schemes, the delegates elected in 1972 "
        "and 1978, and the National Election Commission volumes that list them",
    )
    p.external.add(KOREA)
    for volume, title in TITLES.items():
        p.add(
            "source",
            id=source_id(volume),
            kind="official_body",
            publisher=PUBLISHER,
            title=title,
            url=project(volume),
            license="No license stated: a publication of the National Election Commission; facts published with "
            "attribution",
            license_url="https://library.nec.go.kr",
            attribution=PUBLISHER,
            notes="Page images of the e-book in the Commission's election library; the list of elected delegates "
            "(當選者名簿) and the statistics tables of the National Conference for Unification delegate elections.",
        )
        p.uses(raw.cached(SOURCE, project(volume)))
    for code, rank, kind, noun in SCHEMES:
        p.add("scheme", code=code, polity_id=KOREA, kind=kind, rank=rank, noun=noun, code_system=None)
    body = key("body", "ncu")
    p.add("body", id=body, polity_id=KOREA, kind="electoral_college")
    p.name(body, "통일주체국민회의", "ko", "official")
    p.name(body, "National Conference for Unification", "en", "official", False)
    uncertain = {(volume, page, name) for (volume, page, _), (_, _, name) in REVIEWED.items() if name is not None}
    seen: set[str] = set()
    for district in districts:
        for delegate in [*district.delegates, *([district.revised] if district.revised else [])]:
            who = person(delegate)
            if who in seen:
                continue
            seen.add(who)
            p.add(
                "person",
                id=who,
                birth_date=delegate.born,
                birth_precision="day" if delegate.born else None,
                gender=delegate.sex,
            )
            p.name(who, delegate.row.name, "ko", "official")
            if (delegate.row.volume, delegate.row.page, delegate.row.name) in uncertain:
                text = ILLEGIBLE if "□" in delegate.row.name else UNCERTAIN
                p.add("note", id=key("note", who, "name"), entity_id=who, kind="caveat", body_md=text, lang="en")
    return p


def election_names(day: date, kind: str) -> tuple[str, str]:
    if kind == "general":
        return (
            f"{TERMS[day]} 통일주체국민회의 대의원선거",
            f"{day.year} National Conference for Unification delegate election",
        )
    return (
        f"통일주체국민회의 대의원 재선거 ({day.year}년 {day.month}월 {day.day}일)",
        f"National Conference for Unification delegate rerun of {day:%B} {day.day}, {day.year}",
    )


def build(
    day: date, districts: list[District], turnout: dict[str, list[int]] | None, volume: int, done: set[tuple[int, int]]
) -> Package:
    kind = districts[0].kind
    rerun_of = GENERAL[3] if kind == "by_election" else None
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"South Korea's National Conference for Unification delegates elected on {day}, by district, from the "
        "National Election Commission's 대한민국선거사",
        depends=[REGISTRY, *([f"{SOURCE}/{rerun_of}"] if rerun_of else [])],
    )
    p.external.add(KOREA)
    election = key("election", day)
    p.add(
        "election",
        id=election,
        polity_id=KOREA,
        kind=kind,
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
        rerun_of_id=key("election", rerun_of) if rerun_of else None,
    )
    korean, english = election_names(day, kind)
    p.name(election, korean, "ko", "official")
    p.name(election, english, "en", "official", False)
    units: dict[tuple[str, ...], str] = {}

    def unit(scheme: str, *path: str) -> str:
        if path in units:
            return units[path]
        made = units[path] = key("unit", day, *path)
        p.add(
            "reporting_unit",
            id=made,
            polity_id=KOREA,
            scheme_code=scheme,
            code=f"{day}:" + "/".join(path),
            parent_id=units.get(path[:-1]) if len(path) > 1 else None,
            area_relation="unlocated",
            area_id=None,
            kind="standard",
        )
        p.name(made, province_names(path[0])[0] if len(path) == 1 else " ".join(path[1:]), "ko", "official")
        return made

    for district in districts:
        unit("kr.ncu_sido", district.province)
        unit("kr.ncu_commission", district.province, district.commission)
        place = unit("kr.ncu_district", *district.ref)
        tacit = district.delegates[0].unopposed
        contest = key("contest", day, *district.ref)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=key("body", "ncu"),
            magnitude=district.seats or len(district.delegates),
            round_number=1,
            default_result_kind="tacit" if tacit else "counted",
        )
        p.name(contest, f"통일주체국민회의 대의원 {district.commission} {district.name}", "ko", "official")
        p.name(
            contest,
            f"National Conference for Unification delegates, {district.commission} {district.name}",
            "en",
            "official",
            False,
        )
        sets = [("official", district.delegates, [])]
        if district.revised is not None:
            sets.append(("court_revised", [district.revised], district.delegates))
        options: dict[str, str] = {}
        for stage, winners, losers in sets:
            rs = key("result_set", day, stage, *district.ref)
            p.add(
                "result_set",
                id=rs,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind="tacit" if tacit else "counted",
                source_id=source_id(volume),
                status="active",
                is_default=True,
                completeness=None,
            )
            p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
            if district.misprinted is not None:
                p.add(
                    "note",
                    id=key("note", rs, "seats"),
                    entity_id=rs,
                    kind="caveat",
                    body_md=MISPRINT.format(printed=district.misprinted, listed=len(district.delegates)),
                    lang="en",
                )
            if stage == "court_revised":
                p.add(
                    "note",
                    id=key("note", rs, "revised"),
                    entity_id=rs,
                    kind="context",
                    body_md=f"On July 30, 1979, the winner of this district was redetermined (當選人再決定): "
                    f"{winners[0].row.name} was declared elected with {winners[0].votes or 0:,} votes, as "
                    f"printed in 대한민국선거사 제3집, p. {winners[0].row.page - 1}.",
                    lang="en",
                )
            for delegate in winners:
                if delegate.missing is not None:
                    p.add(
                        "note",
                        id=key("note", rs, person(delegate)),
                        entity_id=rs,
                        kind="caveat",
                        body_md=f"{delegate.row.name}: {delegate.missing}",
                        lang="en",
                    )
            for delegate in [*winners, *losers]:
                who = person(delegate)
                if who not in options:
                    option = options[who] = key("option", day, *district.ref, who)
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        ballot_position=len(options),
                        ballot_label=delegate.row.name,
                    )
                    p.add(
                        "candidacy",
                        id=key("candidacy", day, *district.ref, who),
                        person_id=who,
                        option_id=option,
                        role="head",
                        ballot_name=delegate.row.name,
                    )
                won = delegate in winners
                p.add(
                    "outcome",
                    result_set_id=rs,
                    option_id=options[who],
                    kind="elected" if won else "lost",
                    seats=1 if won else None,
                )
                if not won or delegate.votes is None:
                    continue
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=place,
                    option_id=options[who],
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=DIGITIZED,
                    value=delegate.votes,
                )
                x0, y0, x1, y1 = delegate.row.box
                p.add(
                    "value_locator",
                    result_set_id=rs,
                    unit_id=place,
                    option_id=options[who],
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=cite(p, done, delegate.row.volume, delegate.row.page),
                    x0=x0 / 1000,
                    y0=y0 / 1000,
                    x1=x1 / 1000,
                    y1=y1 / 1000,
                )
    if turnout is not None:
        rs = key("result_set", day, "turnout")
        p.add(
            "result_set",
            id=rs,
            election_id=election,
            contest_id=None,
            stage="official",
            kind="counted",
            source_id=source_id(volume),
            status="active",
            is_default=True,
            completeness=None,
        )
        p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=TURNOUT, lang="en")
        unbalanced = [code for code, *_ in PROVINCES if turnout[code][3] + turnout[code][4] != turnout[code][2]]
        if unbalanced != (["41"] if volume == 2 else []):
            raise ValueError(f"vol. {volume}: ballots do not balance in {unbalanced}")
        if unbalanced:
            p.add("note", id=key("note", rs, "ballots"), entity_id=rs, kind="caveat", body_md=UNBALANCED, lang="en")
            p.accept("accounting.ballots_sum", UNBALANCED)
        page = cite(p, done, volume, min(TABLES[volume]))
        for code, *_ in PROVINCES:
            place = unit("kr.ncu_sido", code)
            for measure, value in zip((2, 3, 4, 5), turnout[code][1:], strict=True):
                p.add(
                    "result_value",
                    result_set_id=rs,
                    unit_id=place,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=DIGITIZED,
                    value=value,
                )
                p.add(
                    "value_locator",
                    result_set_id=rs,
                    unit_id=place,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    page_id=page,
                    x0=0.0,
                    y0=0.0,
                    x1=1.0,
                    y1=1.0,
                )
    return p


def parse() -> None:
    every: list[District] = []
    built: list[tuple[date, list[District], dict[str, list[int]] | None, int]] = []
    for volume in VOLUMES:
        districts, counts = walk(volume)
        turnout = check(volume, districts, counts)
        every += districts
        for day in sorted({d.election for d in districts}):
            mine = [d for d in districts if d.election == day]
            built.append((day, mine, turnout if day == GENERAL[volume] else None, volume))
    registry(every).write()
    cited: dict[int, set[tuple[int, int]]] = {}
    for day, districts, turnout, volume in built:
        general = cited.setdefault(volume, set())
        p = build(day, districts, turnout, volume, general if day == GENERAL[volume] else set(general))
        for code, count, _ in package.check(p.write()):
            print(f"{day}: {code} {count}")


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{day}" for day in DAYS)]


def emit() -> None:
    package.run("load", packages())
