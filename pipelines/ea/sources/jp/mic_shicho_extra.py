"""Designated-city mayoral elections that jp.mic_shicho leaves out: the two scanned summaries of 6 February 2011
(Nagoya, Kitakyushu), which have no text layer, and Yokohama's 18 October 2026 election, whose link on the ministry's
list serves another city's file. The scans are read from their page images and checked against the printed totals;
Yokohama's count comes from the city election commission's result tables, by ward.

The cities, their mayor offices, areas, reporting units, the mayoral electoral system, and the people and parties the
earlier pipeline already minted are referenced by id; this pipeline mints only the elections, their contests, options,
candidacies, results, and the people who stand for the first time.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import uuid5

import polars as pl
from PIL import Image

from ea import package, raw
from ea.package import Package
from ea.sources.jp.mic_chiji import Word, pages, render, size
from ea.store import DATA

SOURCE = "jp.mic_shicho_extra"
INDEX = "https://www.soumu.go.jp/senkyo/senkyo_s/data/shityou/index.html"
PAGES = DATA / "pages" / SOURCE
HERE = Path(__file__).parent
DIGITIZED = 8
MEASURES = {"registered": 2, "voters": 14, "cast": 3, "valid": 4, "invalid": 5}
MIC_LICENSE = "Public Data License 1.0"
MIC_LICENSE_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"
CITY_LICENSE = "No license stated: facts published with attribution, raw files not redistributed"
RULES = {"formula": "fptp", "minimum": {"of": "valid", "share": 0.25}}
JCP = "d4fe13e7-018a-5291-8328-636ff41e81e4"
GENZEI = "1a422341-f531-5c59-8cf2-eb015b38a2bd"
SCAN_METHOD = (
    "The ministry's election summary for this city is a scanned page without a text layer. Its figures were read from "
    "the page image and checked against the printed totals: the candidates' votes add up to the valid votes, the "
    "valid and invalid votes to the ballots cast, and the ballots cast plus the ballots taken away and others to "
    "the voters. They are marked as digitized. The summary gives the city total only."
)
CITY_METHOD = (
    "Final count (開票確定) from the Yokohama City Election Commission's result tables: the candidates' votes by "
    "ward and for the city, the electorate and voters from its turnout table, and the valid and invalid votes and "
    "ballots cast from its table of the ballot breakdown. Voters are the ballots cast plus the ballots taken away "
    "and not accepted."
)


@dataclass(frozen=True)
class City:
    code: str
    english: str
    native: str
    polity: str
    office: str
    area: str
    unit: str
    prefix: str


CITIES = {
    "23100": City(
        "23100",
        "Nagoya",
        "名古屋市",
        "a32e7ef3-c7e0-55eb-90d6-da19edba1148",
        "e3ce226b-6eda-5534-9ecf-640497f52862",
        "ac7e5e9c-df55-5be4-8c7e-09ac46345dc6",
        "488316e6-0e60-5241-af40-b1a4612f1528",
        "jp/aichi/nagoya-city",
    ),
    "40100": City(
        "40100",
        "Kitakyushu",
        "北九州市",
        "358c422a-1410-5c7e-96fd-e536092b320e",
        "07db19d2-5ff4-5534-9d81-c59e148de17f",
        "ba8a1b15-092b-5220-b999-cc2e25251a36",
        "bf8594e6-a5d3-5632-8580-c04f0da84758",
        "jp/fukuoka/kitakyushu-city",
    ),
    "14100": City(
        "14100",
        "Yokohama",
        "横浜市",
        "15e17f67-1ab5-5d3f-9838-a7a7000e9de0",
        "b7124d0c-fcea-5cfe-928f-387941ccf754",
        "aedc678b-ed9d-5b60-922f-0a15fc1dffc6",
        "24f2b2f9-7e0e-5980-aa83-02958eb5e5ae",
        "jp/kanagawa/yokohama-city",
    ),
}
# People the earlier pipeline already holds, by (city code, legal name without spaces).
KNOWN_PEOPLE = {
    ("23100", "河村たかし"): "ff84bcb9-c1b4-50ac-93cb-d14b6fe781d7",
    ("40100", "北橋健治"): "aef72795-6cdb-5378-b49c-e086b16a20ed",
    ("14100", "山中竹春"): "6cced663-917a-54d6-99e1-6451d20b1f9e",
}


@dataclass
class Candidate:
    name: str
    legal: str
    sex: str | None
    party: str | None
    status: str
    votes: str
    won: bool
    box: list[float] | None = None


@dataclass
class Scan:
    code: str
    held: date
    url: str
    figures: dict[str, str]
    boxes: dict[str, list[float]]
    candidates: list[Candidate] = field(default_factory=list)


# Read from the page images (000101517.pdf, 000101516.pdf). Boxes are fractions of the page image.
SCANS = {
    "2011-02-06:23100": Scan(
        "23100",
        date(2011, 2, 6),
        "https://www.soumu.go.jp/main_content/000101517.pdf",
        {"registered": "1776398", "voters": "961694", "cast": "961667", "valid": "948605", "invalid": "13062"},
        {
            "registered": [0.7658, 0.2755, 0.8411, 0.2899],
            "voters": [0.777, 0.2932, 0.8401, 0.3064],
            "cast": [0.3448, 0.6456, 0.4164, 0.6581],
            "valid": [0.3448, 0.6594, 0.4164, 0.6713],
            "invalid": [0.3532, 0.6739, 0.4173, 0.6857],
        },
        [
            Candidate("石田 よしひろ", "石田 芳弘", "男", None, "新", "216764", False, [0.777, 0.4852, 0.8439, 0.4997]),
            Candidate("八田 ひろ子", "八田 廣子", "女", None, "新", "46405", False, [0.7862, 0.5247, 0.8439, 0.5391]),
            Candidate("杉山 ひとし", "杉山 均", "男", None, "新", "23185", False, [0.7862, 0.5654, 0.8439, 0.5799]),
            Candidate(
                "河村 たかし", "河村 たかし", "男", "減税日本", "前", "662251", True, [0.777, 0.6062, 0.8439, 0.6206]
            ),
        ],
    ),
    "2011-02-06:40100": Scan(
        "40100",
        date(2011, 2, 6),
        "https://www.soumu.go.jp/main_content/000101516.pdf",
        {"registered": "798568", "voters": "295470", "cast": "295470", "valid": "291207", "invalid": "4263"},
        {
            "registered": [0.79, 0.311, 0.8569, 0.3228],
            "voters": [0.79, 0.3287, 0.8569, 0.3406],
            "cast": [0.3615, 0.593, 0.4257, 0.6062],
            "valid": [0.3615, 0.6082, 0.4257, 0.6213],
            "invalid": [0.3764, 0.6239, 0.4257, 0.6364],
        },
        [
            Candidate("みわ 俊和", "三輪 俊和", "男", None, "新", "76980", False, [0.8011, 0.5122, 0.855, 0.5253]),
            Candidate("きたはし 健治", "北橋 健治", "男", None, "現", "214227", True, [0.7928, 0.5542, 0.855, 0.568]),
        ],
    ),
}
YOKOHAMA_URL = "https://www.city.yokohama.lg.jp/city-info/senkyo/data/mayor/20261018_shicho.files/R08_shicho_kekka.pdf"
YOKOHAMA_HELD = date(2026, 10, 18)
WARDS = {
    "鶴見区": "14101",
    "神奈川区": "14102",
    "西区": "14103",
    "中区": "14104",
    "南区": "14105",
    "保土ケ谷区": "14106",
    "磯子区": "14107",
    "金沢区": "14108",
    "港北区": "14109",
    "戸塚区": "14110",
    "港南区": "14111",
    "旭区": "14112",
    "緑区": "14113",
    "瀬谷区": "14114",
    "栄区": "14115",
    "泉区": "14116",
    "青葉区": "14117",
    "都筑区": "14118",
}
TOTAL_LABEL = "横浜市・計"


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    for scan in SCANS.values():
        raw.fetch(SOURCE, scan.url, suffix=".pdf")
    raw.fetch(SOURCE, YOKOHAMA_URL, suffix=".pdf")


def ward_units() -> dict[str, str]:
    frame = pl.read_csv(HERE / "city_gikai_wards.tsv", separator="\t", schema_overrides={"code": pl.String})
    return {row["code"]: row["unit_id"] for row in frame.iter_rows(named=True) if row["code"] in WARDS.values()}


@dataclass
class Figure:
    value: str
    page: int
    box: list[float]


@dataclass
class Yokohama:
    path: Path
    sha256: str
    candidates: list[dict[str, str]]
    rows: dict[str, dict[str, Figure]]
    votes: dict[str, list[Figure]]


NUMBER = re.compile(r"^[\d,]+$")


def row_numbers(words: list[Word], label: Word, tolerance: float = 8) -> list[Word]:
    """The whole-number words on a label's row, left to right; decimals and bracketed shares are left out."""
    mid = (label.y0 + label.y1) / 2
    found = [w for w in words if NUMBER.match(w.text) and abs((w.y0 + w.y1) / 2 - mid) < tolerance and w.x0 > label.x1]
    return sorted(found, key=lambda w: w.x0)


def read_yokohama() -> Yokohama:
    artifact = raw.cached(SOURCE, YOKOHAMA_URL)
    width, height = size(artifact.path)
    sheets = pages(artifact.path)
    if len(sheets) != 5:
        raise ValueError(f"expected 5 pages, found {len(sheets)}")

    def figure(page: int, word: Word) -> Figure:
        box = [word.x0 / width, word.y0 / height, word.x1 / width, word.y1 / height]
        return Figure(word.text.replace(",", ""), page, box)

    def label(page: int, text: str) -> Word:
        found = [w for w in sheets[page - 1] if w.text == text and w.x0 < 60]
        if len(found) != 1:
            raise ValueError(f"page {page} has {len(found)} rows for {text}")
        return found[0]

    rows: dict[str, dict[str, Figure]] = {}
    for name in (TOTAL_LABEL, *WARDS):
        rows[name] = {}
        for page, keys in ((3, {2: "registered", 5: "voters"}), (5, {3: "valid", 4: "invalid", 5: "cast"})):
            numbers = row_numbers(sheets[page - 1], label(page, name))
            if len(numbers) < max(keys) + 1:
                raise ValueError(f"page {page} row {name} has {len(numbers)} numbers")
            for index, key in keys.items():
                rows[name][key] = figure(page, numbers[index])
    votes: dict[str, list[Figure]] = {}
    for name in (TOTAL_LABEL, *WARDS):
        numbers = row_numbers(sheets[3], label(4, name), 8)
        # the last number of the row is the ward's total of the candidates' votes
        votes[name] = [figure(4, w) for w in numbers if w.x0 > 100]
        if len(votes[name]) != 8:
            raise ValueError(f"page 4 row {name} has {len(votes[name])} figures; expected 7 candidates and a total")

    def header(low: float, high: float) -> list[Word]:
        return sorted((w for w in sheets[3] if low <= w.y0 <= high and 130 < w.x0 < 640), key=lambda w: w.x0)

    numbers, names, parties = header(111, 113), header(127, 129), header(142, 144)
    if not (len(numbers) == 7 and len(names) == 14 and len(parties) == 14):
        raise ValueError(f"candidate header read as {len(numbers)}, {len(names)}, {len(parties)} words")
    candidates = [
        {
            "number": numbers[i].text,
            "name": f"{names[2 * i].text} {names[2 * i + 1].text}",
            "party": parties[2 * i].text,
            "status": parties[2 * i + 1].text,
        }
        for i in range(7)
    ]
    return Yokohama(artifact.path, artifact.sha256, candidates, rows, votes)


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Sources of the designated-city mayoral elections the ministry's summaries leave out",
    )
    system = p.id("system", "mayor")
    p.add(
        "electoral_system",
        id=system,
        polity_id=str(uuid5(package.NAMESPACE, "polity\x1fJP")),
        rules=RULES,
        rules_version=1,
    )
    p.external.add(str(uuid5(package.NAMESPACE, "polity\x1fJP")))
    p.name(system, "First past the post with a legal minimum of a quarter of the valid votes", "en", "official")
    p.name(system, "相対多数制（法定得票数は有効投票総数の4分の1）", "ja", "native", False)
    p.add(
        "source",
        id=p.id("source", "mic"),
        kind="official_body",
        publisher="総務省",
        title="指定都市市長選挙 選挙結果調 (scanned summaries)",
        url=INDEX,
        license=MIC_LICENSE,
        license_url=MIC_LICENSE_URL,
        attribution="出典：総務省ホームページ",
        notes="The ministry's summaries of the designated-city mayoral elections of 6 February 2011 are scanned pages "
        "without a text layer; their figures were read from the page images.",
    )
    p.add(
        "source",
        id=p.id("source", "yokohama"),
        kind="official_body",
        publisher="横浜市選挙管理委員会",
        title="横浜市長選挙結果表 (2026-10-18)",
        url=YOKOHAMA_URL,
        license=CITY_LICENSE,
        license_url=None,
        attribution="出典：横浜市選挙管理委員会",
        notes="Final count tables by ward, read from the commission's PDF. The ministry's list links this election "
        "to another city's file.",
    )
    return p


def page_image(path: Path, sha256: str, number: int) -> tuple[str, int, int]:
    PAGES.mkdir(parents=True, exist_ok=True)
    image = PAGES / f"{sha256}-{number}.png"
    if not image.exists():
        render(path, number, 100).save(image)
    width, height = Image.open(image).size
    return f"pages/{SOURCE}/{image.name}", width, height


@dataclass
class Entry:
    option: str
    measure: int
    unit: str
    value: str
    page: int
    box: list[float]


class Build:
    """One package's mayoral election: rows common to the scans and Yokohama."""

    def __init__(self, key: str, held: date, city: City, source: str, url: str, summary: str, method: str) -> None:
        self.p = Package(f"{SOURCE}/{key}", SOURCE, summary, depends=[f"{SOURCE}/registry"])
        self.key, self.held, self.city, self.source, self.method = key, held, city, source, method
        self.artifact = self.p.artifact(raw.cached(SOURCE, url), source)
        self.path = raw.cached(SOURCE, url).path
        self.sha256 = raw.cached(SOURCE, url).sha256
        self.pages: dict[int, str] = {}
        self.locators: list[dict[str, object]] = []
        self.people: set[str] = set()
        city_ = self.city
        p = self.p
        p.external |= {city_.polity, city_.office, city_.area, city_.unit}
        self.election = p.id("election", key)
        p.add(
            "election",
            id=self.election,
            polity_id=city_.polity,
            kind="general",
            date_from=held,
            date_to=held,
            status="held",
        )
        p.name(self.election, f"{held.year} {city_.english} mayoral election", "en", "official")
        p.name(self.election, f"{city_.native}長選挙", "ja", "native", False)
        path = f"{city_.prefix}/{held}-mayor"
        p.add("slug", path=path, entity_id=self.election, is_current=True)
        self.contest = p.id("contest", key)
        p.add(
            "contest",
            id=self.contest,
            election_id=self.election,
            kind="office",
            office_id=city_.office,
            district_area_id=city_.area,
            magnitude=1,
            round_number=1,
            electoral_system_id=p.id("system", "mayor"),
            default_result_kind="counted",
        )
        p.name(self.contest, f"{held.year} election for Mayor of {city_.english}", "en", "official")
        p.add("slug", path=f"{path}/mayor", entity_id=self.contest, is_current=True)
        self.result_set = p.id("result_set", key)
        p.add(
            "result_set",
            id=self.result_set,
            election_id=self.election,
            contest_id=self.contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1,
        )
        p.add(
            "note",
            id=p.id("note", self.result_set),
            entity_id=self.result_set,
            kind="methodology",
            body_md=method,
            lang="en",
        )

    def page(self, number: int) -> str:
        if number not in self.pages:
            image, width, height = page_image(self.path, self.sha256, number)
            id = self.p.id("page", self.sha256, number)
            self.p.add(
                "source_page",
                id=id,
                artifact_id=self.artifact,
                page_number=number,
                image_key=image,
                width=width,
                height=height,
            )
            self.pages[number] = id
        return self.pages[number]

    def value(
        self, unit: str, option: str | None, measure: int, amount: str, page: int, box: list[float], flags: int
    ) -> None:
        self.p.add(
            "result_value",
            result_set_id=self.result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=flags,
            value=Decimal(amount),
        )
        self.locators.append(
            {
                "result_set_id": self.result_set,
                "unit_id": unit,
                "option_id": option,
                "channel": 0,
                "measure": measure,
                "count_round": 0,
                "page_id": self.page(page),
                "source_id": None,
                "x0": box[0],
                "y0": box[1],
                "x1": box[2],
                "y1": box[3],
            }
        )

    def candidate(
        self,
        number: int,
        name: str,
        legal: str,
        sex: str | None,
        party: str | None,
        incumbent: bool,
        won: bool,
    ) -> str:
        p = self.p
        compact = re.sub(r"\s+", "", legal)
        known = KNOWN_PEOPLE.get((self.city.code, compact))
        if known is not None:
            person = known
            p.external.add(person)
        else:
            person = p.id("person", f"{self.city.code}:{compact}")
            p.add("person", id=person, gender={"男": "male", "女": "female"}.get(sex or ""))
            p.name(person, legal, "ja", "official")
        if party is not None:
            p.external.add(party)
        option = p.id("option", f"{self.key}:{number}")
        p.add(
            "contest_option",
            id=option,
            contest_id=self.contest,
            kind="candidate",
            party_id=party,
            ballot_position=number,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=p.id("candidacy", f"{self.key}:{number}"),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
            member_of_party_id=party,
            is_independent=party is None,
            is_incumbent=incumbent,
        )
        p.add(
            "outcome",
            result_set_id=self.result_set,
            option_id=option,
            kind="won" if won else "lost",
            seats=1 if won else None,
        )
        return option

    def write(self) -> Path:
        for locator in self.locators:
            self.p.add("value_locator", **locator)
        return self.p.write()


def scan(key: str) -> Build:
    s = SCANS[key]
    city = CITIES[s.code]
    figures, votes = s.figures, [int(c.votes) for c in s.candidates]
    if sum(votes) != int(figures["valid"]):
        raise ValueError(f"{key}: candidates add up to {sum(votes)}, valid votes are {figures['valid']}")
    if int(figures["valid"]) + int(figures["invalid"]) != int(figures["cast"]):
        raise ValueError(f"{key}: valid and invalid votes do not add up to the ballots cast")
    if [c.won for c in s.candidates].count(True) != 1 or not max(s.candidates, key=lambda c: int(c.votes)).won:
        raise ValueError(f"{key}: the declared winner is not the leading candidate")
    p_source = Package(f"{SOURCE}/registry", SOURCE, "").id("source", "mic")
    b = Build(
        f"{s.held}-{city.english.lower()}",
        s.held,
        city,
        p_source,
        s.url,
        f"{city.english} mayoral election of {s.held}, city total, from the ministry's scanned summary",
        SCAN_METHOD,
    )
    for measure_key, measure in MEASURES.items():
        b.value(city.unit, None, measure, figures[measure_key], 1, s.boxes[measure_key], DIGITIZED)
    for number, c in enumerate(s.candidates, 1):
        party = {"減税日本": GENZEI}.get(c.party or "")
        option = b.candidate(number, c.name, c.legal, c.sex, party, c.status == "現", c.won)
        assert c.box is not None
        b.value(city.unit, option, 1, c.votes, 1, c.box, DIGITIZED)
    return b


def yokohama() -> Build:
    y = read_yokohama()
    city = CITIES["14100"]
    units = ward_units()
    p_source = Package(f"{SOURCE}/registry", SOURCE, "").id("source", "yokohama")
    b = Build(
        f"{YOKOHAMA_HELD}-yokohama",
        YOKOHAMA_HELD,
        city,
        p_source,
        YOKOHAMA_URL,
        "Yokohama mayoral election of 2026-10-18, by ward, from the city election commission's result tables",
        CITY_METHOD
        + " The tables do not mark the winner; the candidate with the most votes, above the legal minimum of "
        "a quarter of the valid votes the table prints, is the one elected.",
    )
    for rows in (y.rows,):
        for name, row in rows.items():
            if int(row["valid"].value) + int(row["invalid"].value) != int(row["cast"].value):
                raise ValueError(f"{name}: valid and invalid votes do not add up to the ballots cast")
            if int(row["voters"].value) < int(row["cast"].value):
                raise ValueError(f"{name}: fewer voters than ballots cast")
    for key in ("registered", "voters", "valid", "invalid", "cast"):
        wards = sum(int(y.rows[name][key].value) for name in WARDS)
        if wards != int(y.rows[TOTAL_LABEL][key].value):
            raise ValueError(f"wards add up to {wards} {key}, the city total is {y.rows[TOTAL_LABEL][key].value}")
    totals = y.votes[TOTAL_LABEL]
    leader = max(range(7), key=lambda i: int(totals[i].value))
    if int(totals[leader].value) * 4 < int(y.rows[TOTAL_LABEL]["valid"].value):
        raise ValueError("the leader is below the legal minimum")
    options = []
    for i, c in enumerate(y.candidates):
        party = JCP if c["party"] == "日本共産党" else None
        if c["party"] not in ("無所属", "日本共産党"):
            raise ValueError(f"unmapped party {c['party']}")
        options.append(
            b.candidate(int(c["number"]), c["name"], c["name"], None, party, c["status"] == "現", i == leader)
        )
    for name in (TOTAL_LABEL, *WARDS):
        unit = city.unit if name == TOTAL_LABEL else units[WARDS[name]]
        b.p.external.add(unit)
        row = y.rows[name]
        shown = y.votes[name]
        if sum(int(f.value) for f in shown[:7]) != int(shown[7].value) or int(shown[7].value) != int(
            row["valid"].value
        ):
            raise ValueError(f"{name}: candidates' votes do not add up to the valid votes")
        for key, measure in MEASURES.items():
            b.value(unit, None, measure, row[key].value, row[key].page, row[key].box, 0)
        for option, f in zip(options, shown[:7], strict=True):
            b.value(unit, option, 1, f.value, f.page, f.box, 0)
    for wards, label in ((sum(int(y.votes[n][i].value) for n in WARDS), i) for i in range(7)):
        if wards != int(totals[label].value):
            raise ValueError(f"candidate {label}: wards add up to {wards}, the city total is {totals[label].value}")
    return b


def builds() -> list[Build]:
    return [scan(key) for key in SCANS] + [yokohama()]


def parse() -> None:
    registry().write()
    for b in builds():
        b.write()


def keys() -> list[str]:
    return [f"{SOURCE}/registry", *(b.p.key for b in builds())]


def emit() -> None:
    package.run("load", keys())
