"""Iran's national referendums: 1953, 1963, March 1979, December 1979, and 1989.

The Ministry of Interior's own hosts refuse automated requests, so the figures are read from the Wikipedia article on
each vote. The March 1979 article reproduces the Ministry of Interior's table of yes and no votes in each of the 24
provinces of the time and among Iranians abroad; its provincial rows add up exactly to the totals the Ministry
printed. The other four articles give national totals, from Nohlen, Grotz and Hartmann's data handbook and from
Direct Democracy. The Iran Data Portal's index of the three referendums after the revolution links to no figures.
"""

import html
import json
import re
from dataclasses import dataclass
from datetime import date
from uuid import uuid5

import polars as pl
import shapely
import shapely.geometry

from ea import package, raw
from ea.package import Package
from ea.sources.ir import moi

SOURCE = "ir.referendum"
IRAN = moi.IRAN
COUNTRY = moi.COUNTRY
WIKI = "https://en.wikipedia.org/wiki/"
LICENSE = "CC BY-SA 4.0"
LICENSE_URL = "https://creativecommons.org/licenses/by-sa/4.0/"
VINTAGE = "1979"
VINTAGE_DAY = date(1979, 3, 30)

# The provinces of 1979, as the Ministry's table names them: key, English name, Persian name, and the provinces of
# 2017 (keys of `moi.PROVINCES`) that together cover its territory.
PROVINCES: dict[str, tuple[str, str, tuple[str, ...]]] = {
    "east-azerbaijan": ("East Azerbaijan", "آذربایجان شرقی", ("east-azerbaijan", "ardabil")),
    "west-azerbaijan": ("West Azerbaijan", "آذربایجان غربی", ("west-azerbaijan",)),
    "isfahan": ("Isfahan", "اصفهان", ("isfahan",)),
    "ilam": ("Ilam", "ایلام", ("ilam",)),
    "bakhtaran": ("Bakhtaran", "باختران", ("kermanshah",)),
    "bushehr": ("Bushehr", "بوشهر", ("bushehr",)),
    "tehran": ("Tehran", "تهران", ("tehran", "alborz", "qom", "qazvin")),
    "chaharmahal-and-bakhtiari": ("Chaharmahal and Bakhtiari", "چهارمحال و بختیاری", ("chaharmahal-and-bakhtiari",)),
    "khorasan": ("Khorasan", "خراسان", ("razavi-khorasan", "north-khorasan", "south-khorasan")),
    "khuzestan": ("Khuzestan", "خوزستان", ("khuzestan",)),
    "zanjan": ("Zanjan", "زنجان", ("zanjan",)),
    "semnan": ("Semnan", "سمنان", ("semnan",)),
    "sistan-and-baluchestan": ("Sistan and Baluchestan", "سیستان و بلوچستان", ("sistan-and-baluchestan",)),
    "fars": ("Fars", "فارس", ("fars",)),
    "kurdistan": ("Kurdistan", "کردستان", ("kurdistan",)),
    "kerman": ("Kerman", "کرمان", ("kerman",)),
    "kohgiluyeh-and-boyer-ahmad": (
        "Kohgiluyeh and Boyer-Ahmad",
        "کهگیلویه و بویراحمد",
        ("kohgiluyeh-and-boyer-ahmad",),
    ),
    "gilan": ("Gilan", "گیلان", ("gilan",)),
    "lorestan": ("Lorestan", "لرستان", ("lorestan",)),
    "mazandaran": ("Mazandaran", "مازندران", ("mazandaran", "golestan")),
    "markazi": ("Markazi", "مرکزی", ("markazi",)),
    "hormozgan": ("Hormozgan", "هرمزگان", ("hormozgan",)),
    "hamadan": ("Hamadan", "همدان", ("hamadan",)),
    "yazd": ("Yazd", "یزد", ("yazd",)),
}
# The article's spelling of each province, in its own order.
SPELLINGS = {
    "East Azerbaijan province": "east-azerbaijan",
    "West Azerbaijan province": "west-azerbaijan",
    "Isfahan province": "isfahan",
    "Ilam province": "ilam",
    "Bakhtaran Province": "bakhtaran",
    "Bushehr province": "bushehr",
    "Tehran province": "tehran",
    "Chaharmahal and Bakhtiari province": "chaharmahal-and-bakhtiari",
    "Khorasan Province": "khorasan",
    "Khuzestan province": "khuzestan",
    "Zanjan province": "zanjan",
    "Semnan province": "semnan",
    "Sistan and Baluchestan province": "sistan-and-baluchestan",
    "Fars province": "fars",
    "Kurdistan province": "kurdistan",
    "Kerman province": "kerman",
    "Kohgiluyeh and Boyer-Ahmad province": "kohgiluyeh-and-boyer-ahmad",
    "Gilan province": "gilan",
    "Lorestan province": "lorestan",
    "Mazandaran province": "mazandaran",
    "Markazi province": "markazi",
    "Hormozgan province": "hormozgan",
    "Hamedan Province": "hamadan",
    "Yazd province": "yazd",
}
ABROAD = "abroad"


@dataclass(frozen=True)
class Vote:
    day: date
    last: date
    page: str
    en: str
    fa: str
    question: str
    book: str
    yes: int
    no: int
    valid: int
    invalid: int | None
    registered: int | None = None
    source: str = "Nohlen, Grotz and Hartmann, Elections in Asia: A Data Handbook (2001)"


VOTES = [
    Vote(
        date(1953, 8, 3),
        date(1953, 8, 10),
        "1953_Iranian_parliamentary_dissolution_referendum",
        "1953 Iranian parliamentary dissolution referendum",
        "همه‌پرسی انحلال مجلس هفدهم",
        "Dissolution or continuation of the 17th National Consultative Assembly",
        "Tehran voted from 3 August and the provinces on 10 August 1953. The opposition boycotted the vote, which "
        "was held in separate booths for and against, and observers called its result fraudulent. The cities' "
        "partial figures in the article are not taken up. The article's source is Direct Democracy.",
        2_043_389,
        1_207,
        2_044_596,
        4,
        source="Direct Democracy",
    ),
    Vote(
        date(1963, 1, 26),
        date(1963, 1, 26),
        "1963_Iranian_referendum",
        "1963 Iranian referendum on the White Revolution",
        "همه‌پرسی انقلاب سفید",
        "The six-point reform program, the White Revolution of the Shah and the People: abolition of the "
        "landowner-tenant system by land reform, nationalization of forests and pastures, sale of government "
        "factories to finance land reform, workers as shareholders of factories, reform of the electoral laws, and "
        "creation of the Literacy Corps",
        "The article's figures are from Nohlen, Grotz and Hartmann and from Zonis; it lists no abstentions or "
        "invalid ballots.",
        5_589_711,
        4_115,
        5_593_826,
        0,
        6_098_277,
        source="Nohlen, Grotz and Hartmann, Elections in Asia: A Data Handbook (2001), and Zonis",
    ),
    Vote(
        date(1979, 3, 30),
        date(1979, 3, 31),
        "1979_Iranian_Islamic_Republic_referendum",
        "1979 Iranian Islamic Republic referendum",
        "همه‌پرسی جمهوری اسلامی",
        "Changing the former regime to an Islamic Republic, the constitution of which shall be submitted to the "
        "nation for approval",
        "",
        20_286_353,
        153_755,
        20_440_108,
        None,
        source="Ministry of Interior",
    ),
    Vote(
        date(1979, 12, 2),
        date(1979, 12, 3),
        "1979_Iranian_constitutional_referendum",
        "1979 Iranian constitutional referendum",
        "همه‌پرسی قانون اساسی جمهوری اسلامی ایران",
        "Do you approve the Constitution of the Islamic Republic of Iran?",
        "Opposition groups called for a boycott. The registered voters the article gives, about 22 million, are "
        "approximate and not taken up.",
        15_680_329,
        78_516,
        15_758_845,
        111,
    ),
    Vote(
        date(1989, 7, 28),
        date(1989, 7, 28),
        "1989_Iranian_constitutional_referendum",
        "1989 Iranian constitutional referendum",
        "همه‌پرسی بازنگری قانون اساسی جمهوری اسلامی ایران",
        "Do you approve the amendments to the Constitution of the Islamic Republic of Iran?",
        "Held on the day of the presidential election.",
        16_025_459,
        398_867,
        16_424_326,
        32_445,
    ),
]
BY_DAY = {vote.day: vote for vote in VOTES}
MARCH = date(1979, 3, 30)
# The totals the Ministry of Interior printed for the March 1979 referendum, which the provinces must add up to.
MARCH_TOTAL = (20_286_353, 153_755)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


GB_SOURCE = key("source", "geoboundaries")


def url(vote: Vote) -> str:
    return WIKI + vote.page


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{vote.day}" for vote in VOTES)]


def fetch() -> None:
    for vote in VOTES:
        raw.fetch(SOURCE, url(vote), suffix=".html")
    raw.fetch(moi.GEOBOUNDARIES, moi.ADM1, suffix=".geojson")


def parse() -> None:
    geo().write()
    registry().write()
    for vote in VOTES:
        election(vote).write()


def emit() -> None:
    package.run("load", packages())


# Reading the article


def table_rows(page: str) -> list[list[str]]:
    rows = []
    for tr in re.findall(r"<tr\b.*?</tr>", page, flags=re.DOTALL):
        cells = re.findall(r"<t[dh]\b.*?</t[dh]>", tr, flags=re.DOTALL)
        texts = [html.unescape(re.sub(r"<[^>]+>", "", cell)).strip() for cell in cells]
        rows.append([re.sub(r"\[[^\]]*\]|\s+", " ", text).strip() for text in texts])
    return rows


def count(text: str) -> int:
    return int(text.replace(",", ""))


def provinces_march() -> dict[str, tuple[int, int]]:
    """Each province's yes and no votes in March 1979, and those abroad, from the article's table."""
    page = raw.cached(SOURCE, url(BY_DAY[MARCH])).path.read_text("utf-8")
    start = page.index("Results by province")
    found: dict[str, tuple[int, int]] = {}
    for row in table_rows(page[start:]):
        if len(row) != 5 or not re.fullmatch(r"[\d,]+", row[1]):
            continue
        name = row[0]
        if name == "Total":
            if (count(row[1]), count(row[2])) != MARCH_TOTAL:
                raise ValueError(f"the printed total {row[1:3]} is not the Ministry's {MARCH_TOTAL}")
            continue
        code = ABROAD if name == "Abroad" else SPELLINGS[name]
        found[code] = (count(row[1]), count(row[2]))
    if set(found) != {*PROVINCES, ABROAD}:
        raise ValueError(f"provinces differ: {sorted(set(found) ^ {*PROVINCES, ABROAD})}")
    yes = sum(y for y, _ in found.values())
    no = sum(n for _, n in found.values())
    if (yes, no) != MARCH_TOTAL:
        raise ValueError(f"the provinces add up to {yes} and {no}, not {MARCH_TOTAL}")
    return found


def national(vote: Vote) -> tuple[int, int]:
    """The national yes and no votes the article's results table prints for the vote."""
    page = raw.cached(SOURCE, url(vote)).path.read_text("utf-8")
    seen = {}
    for row in table_rows(page):
        if len(row) >= 2 and re.fullmatch(r"[\d,]+", row[1]) and row[0] in ("For", "Against", "Yes", "No"):
            seen.setdefault(row[0], count(row[1]))
    yes = seen.get("For", seen.get("Yes"))
    no = seen.get("Against", seen.get("No"))
    if yes is None or no is None:
        raise ValueError(f"no yes and no votes in the article for {vote.day}")
    return yes, no


# Packages


def province_area(code: str) -> str:
    return key("area", "ir.ostan", f"{VINTAGE}:{code}")


def geo() -> Package:
    p = Package(
        f"{SOURCE}/geo",
        SOURCE,
        "The 24 provinces of 1979, each built from the provinces of 2017 that cover its territory",
        depends=[f"{moi.SOURCE}/geo"],
    )
    p.external |= {IRAN, COUNTRY}
    artifact = raw.cached(moi.GEOBOUNDARIES, moi.ADM1)
    p.add(
        "source",
        id=GB_SOURCE,
        kind="academic",
        publisher="geoBoundaries (OpenStreetMap, Wambacher)",
        title="geoBoundaries gbOpen Iran ADM1, 2017",
        url="https://www.geoboundaries.org/",
        license="ODbL 1.0 (OpenStreetMap contributors)",
        license_url="https://opendatacommons.org/licenses/odbl/1-0/",
        attribution="geoBoundaries (William & Mary geoLab), © OpenStreetMap contributors",
    )
    p.artifact(artifact, GB_SOURCE)
    p.add("scheme", code="ir.nation", polity_id=IRAN, kind="administrative", rank=0, noun="country")
    p.add(
        "boundary_set",
        id=key("boundary_set", "ir.ostan", VINTAGE),
        scheme_code="ir.ostan",
        vintage=VINTAGE,
        as_of=VINTAGE_DAY,
    )
    shapes: dict[str, list[shapely.Geometry]] = {}
    for feature in json.loads(artifact.path.read_text("utf-8"))["features"]:
        shapes.setdefault(str(feature["properties"]["shapeName"]), []).append(
            shapely.geometry.shape(feature["geometry"])
        )
    used: list[str] = []
    for code, (english, persian, parts) in PROVINCES.items():
        id = province_area(code)
        p.add("area", id=id, scheme_code="ir.ostan")
        p.add("area_code", area_id=id, code=f"{VINTAGE}:{code}")
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, f"استان {persian}", "fa", "native")
        p.name(id, f"{english} Province", "en", "english", False)
        pieces = [shape for part in parts for shape in shapes[moi.PROVINCES[part][0]]]
        used += parts
        shape = shapely.make_valid(shapely.union_all(pieces))
        shape = shapely.MultiPolygon([g for g in shapely.get_parts(shape) if shapely.get_type_id(g) == 3])
        shape = shapely.set_srid(shape, 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(shape, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(shape), 4326), hex=True, include_srid=True
            ),
            quality="osm",
            source_id=GB_SOURCE,
        )
    if sorted(used) != sorted(moi.PROVINCES):
        raise ValueError(f"the 1979 provinces cover {sorted(set(moi.PROVINCES) ^ set(used))} wrongly")
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The rule of Iran's national referendums: the option with most valid votes carries",
    )
    p.external.add(IRAN)
    id = key("electoral_system", "referendum")
    p.add("electoral_system", id=id, polity_id=IRAN, rules={"formula": "referendum"}, rules_version=1)
    p.add(
        "note",
        id=key("note", id),
        entity_id=id,
        kind="methodology",
        body_md=(
            "A yes or no vote on one question, carried by the option with most valid votes. The archive applies "
            "this rule to all five national referendums, from 1953 to 1989, whatever the law of the day required."
        ),
        lang="en",
    )
    return p


def election(vote: Vote) -> Package:
    p = Package(
        f"{SOURCE}/{vote.day}",
        SOURCE,
        f"The {vote.en}: yes and no votes " + ("in each province and abroad" if vote.day == MARCH else "nationally"),
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry", f"{moi.SOURCE}/geo"],
    )
    p.external |= {IRAN, COUNTRY}
    day = vote.day
    by_province = provinces_march() if day == MARCH else None
    yes, no = national(vote) if by_province is None else MARCH_TOTAL
    if (yes, no) != (vote.yes, vote.no) or yes + no != vote.valid:
        raise ValueError(f"{day}: the article prints {yes} and {no}, not {vote.yes} and {vote.no}")
    source = p.id("source", day)
    if day == MARCH:
        p.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=f"{vote.en} (Wikipedia), reproducing the Ministry of Interior's results by province",
            url=url(vote),
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution="Wikipedia contributors",
            notes=(
                "The article's table of results by province cites the Ministry of Interior of the Islamic Republic "
                "of Iran, whose own page it links is dead and whose hosts refuse automated requests. The provinces "
                "add up exactly to the totals the Ministry printed, 20,286,353 yes and 153,755 no, and their sum "
                "of 20,440,108 is the number of actual voters the Iran Data Portal gives."
            ),
        )
    else:
        p.add(
            "source",
            id=source,
            kind="community",
            publisher="Wikipedia",
            title=f"{vote.en} (Wikipedia), citing {vote.source}",
            url=url(vote),
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution="Wikipedia contributors",
            notes="National totals only; the Ministry of Interior's own pages are not reachable.",
        )
    p.artifact(raw.cached(SOURCE, url(vote)), source)

    election_id = p.id("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=IRAN,
        kind="referendum",
        date_from=day,
        date_to=vote.last,
        date_precision="day",
        status="held",
    )
    p.name(election_id, vote.en, "en", "english")
    p.name(election_id, vote.fa, "fa", "native", False)
    base = f"ir/{day}-referendum"
    p.add("slug", path=base, entity_id=election_id, is_current=True)

    contest = p.id("contest", day)
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="ballot_question",
        electoral_system_id=key("electoral_system", "referendum"),
        round_number=1,
        default_result_kind="counted",
    )
    p.add("ballot_question", contest_id=contest, type="referendum")
    p.name(contest, vote.question, "en", "english")
    p.name(contest, vote.fa, "fa", "native", False)
    p.add("slug", path=f"{base}/question", entity_id=contest, is_current=True)
    options = []
    for position, label in enumerate(("Yes", "No"), 1):
        option = p.id("option", day, position)
        options.append(option)
        p.add(
            "contest_option", id=option, contest_id=contest, kind="answer", ballot_position=position, ballot_label=label
        )

    result_set = p.id("result_set", day)
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
        completeness=1.0,
    )
    if by_province is not None:
        method = (
            "Yes and no votes in each of the 24 provinces of 1979 and among Iranians abroad, as the Ministry of "
            "Interior's table of results gives them in the Wikipedia article. The Ministry's table prints no valid, "
            "invalid, or registered figures by province. Polling took place on 30 and 31 March 1979. Other "
            "sources give different national totals, such as the 20,147,855 yes and 140,996 no of Nohlen, Grotz and "
            "Hartmann; the Ministry's are used here because they are the ones with provincial detail. Provinces are "
            "drawn from the provinces of 2017 that cover their territory; Kurdistan and Turkmen Sahra, where the "
            "vote was not fully held, are as reported."
        )
    else:
        method = (
            f"National totals only, as given by {vote.source} in the Wikipedia article; no provincial results are "
            f"published in a source this archive could reach. " + vote.book
        ).strip()
    p.add("note", id=p.id("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
    p.add("outcome", result_set_id=result_set, option_id=options[0], kind="won")

    rows: list[tuple[str, str | None, int, int]] = []

    def unit(code: str, scheme: str, name_en: str, name_fa: str | None, area: str | None, kind: str) -> str:
        id = p.id("unit", day, code)
        p.add(
            "reporting_unit",
            id=id,
            polity_id=IRAN,
            scheme_code=scheme,
            code=f"{day}:{code}",
            area_relation="equals" if area else "unlocated",
            area_id=area,
            kind=kind,
        )
        p.name(id, name_en, "en", "english")
        if name_fa:
            p.name(id, name_fa, "fa", "native", False)
        return id

    if by_province is None:
        id = unit("national", "ir.nation", "Iran", "ایران", COUNTRY, "aggregate")
        rows += [(id, options[0], 1, vote.yes), (id, options[1], 1, vote.no), (id, None, 4, vote.valid)]
        if vote.invalid is not None:
            rows += [(id, None, 5, vote.invalid), (id, None, 3, vote.valid + vote.invalid)]
        if vote.registered is not None:
            rows.append((id, None, 2, vote.registered))
    else:
        for code, (y, n) in by_province.items():
            if code == ABROAD:
                id = unit(code, "ir.nation", "Iranians abroad", "ایرانیان خارج از کشور", None, "aggregate")
            else:
                english, persian, _ = PROVINCES[code]
                id = unit(code, "ir.ostan", f"{english} Province", f"استان {persian}", province_area(code), "standard")
            rows += [(id, options[0], 1, y), (id, options[1], 1, n)]
    p.frame(
        "result_value",
        pl.DataFrame(rows, schema=["unit_id", "option_id", "measure", "value"], orient="row").with_columns(
            result_set_id=pl.lit(result_set),
            channel=pl.lit(0, pl.Int16),
            count_round=pl.lit(0, pl.Int16),
            flags=pl.lit(0, pl.Int16),
            measure=pl.col("measure").cast(pl.Int16),
        ),
    )
    return p
