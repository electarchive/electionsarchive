import re
import unicodedata
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.jp.mic_chiji import (
    FIGURES,
    candidates,
    checked,
    cleaned,
    digits,
    figures,
    layout,
    locate,
    pages,
    party_key,
    render,
    size,
)
from ea.store import DATA, connect

SOURCE = "jp.mic_shicho"
INDEX = "https://www.soumu.go.jp/senkyo/senkyo_s/data/shityou/index.html"
ROOT = "https://www.soumu.go.jp"
ERAS = {"令和": 2018, "平成": 1988}
LINK = re.compile(r'href="(/main_content/\d+\.pdf)"[^>]*>([^<]*)')
LABEL = re.compile(r"(令和|平成)(元|\d+)年(\d+)月(\d+)日執行(\S+?市)市長選挙")
CITIES = {
    "札幌市": ("01100", "Sapporo"),
    "仙台市": ("04100", "Sendai"),
    "さいたま市": ("11100", "Saitama"),
    "千葉市": ("12100", "Chiba"),
    "横浜市": ("14100", "Yokohama"),
    "川崎市": ("14130", "Kawasaki"),
    "相模原市": ("14150", "Sagamihara"),
    "新潟市": ("15100", "Niigata"),
    "静岡市": ("22100", "Shizuoka"),
    "浜松市": ("22130", "Hamamatsu"),
    "名古屋市": ("23100", "Nagoya"),
    "京都市": ("26100", "Kyoto"),
    "大阪市": ("27100", "Osaka"),
    "堺市": ("27140", "Sakai"),
    "神戸市": ("28100", "Kobe"),
    "岡山市": ("33100", "Okayama"),
    "広島市": ("34100", "Hiroshima"),
    "北九州市": ("40100", "Kitakyushu"),
    "福岡市": ("40130", "Fukuoka"),
    "熊本市": ("43100", "Kumamoto"),
}
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE


def listing() -> list[tuple[str, str, str]]:
    text = raw.cached(SOURCE, INDEX).path.read_bytes().decode("cp932")
    rows = []
    for href, label in LINK.findall(text):
        label = re.sub(r"\s+", "", unicodedata.normalize("NFKC", label.replace("&nbsp;", " ")))
        if not label:
            continue
        if not (match := LABEL.search(label)):
            raise ValueError(f"unread label {label!r}")
        era, year, month, day, city = match.groups()
        held = f"{ERAS[era] + (1 if year == '元' else int(year)):04d}-{int(month):02d}-{int(day):02d}"
        rows.append((ROOT + href, held, city))
    return rows


def fetch() -> None:
    raw.fetch(SOURCE, INDEX, suffix=".html")
    for url, *_ in listing():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(parents=True, exist_ok=True)
    elections, people = [], []
    for url, held, city in listing():
        artifact = raw.cached(SOURCE, url)
        words = pages(artifact.path)
        text = layout(artifact.path)
        flat = re.sub(r"\s+", "", text)
        if not words[0]:
            print(f"{held} {city}: {url} is a scan without a text layer; skipped")
            continue
        day = re.search(r"選挙期日(令和|平成)(元|\d+)年(\d+)月(\d+)日", flat)
        if day is None:
            raise ValueError(f"{url} prints no election day")
        era, year, month, mday = day.groups()
        printed = f"{ERAS[era] + (1 if year == '元' else int(year)):04d}-{int(month):02d}-{int(mday):02d}"
        if f"{city}長選挙" not in flat or printed != held:
            print(f"{held} {city}: {url} prints another election ({printed}); skipped")
            continue
        key = f"{held}:{city}"
        width, height = size(artifact.path)
        found = [
            (page, digits(w.text), [w.x0 / width, w.y0 / height, w.x1 / width, w.y1 / height])
            for page, page_words in enumerate(words, 1)
            for w in page_words
            if re.match(r"^-?[\d,]+(?:\.\d+)?[人票]?$", w.text)
        ]
        figures_ = figures(text)
        rows = candidates(words, width, height)
        for row in rows:
            row["name"], row["legal_name"] = cleaned(row["name"]), cleaned(row["legal_name"])
        cast, valid, invalid = (int(figures_[k] or 0) for k in ("cast", "valid", "invalid"))
        if cast != valid + invalid and cast == valid + invalid + int(figures_["unreturned"] or 0):
            print(f"{key}: ballots cast {cast} also count the {figures_['unreturned']} unreturned, as printed")
            checked(key, {**figures_, "cast": str(valid + invalid)}, rows, False)
        else:
            checked(key, figures_, rows, False)
        boxes = {}
        for k in FIGURES:
            page, box = locate(found, figures_[k])
            boxes[f"{k}_page"], boxes[f"{k}_box"] = page, box
        image = PAGES / f"{artifact.sha256}-1.png"
        if not image.exists():
            render(artifact.path, 1, 100).save(image)
        elections.append(
            {
                "key": key,
                "url": url,
                "sha256": artifact.sha256,
                "held": held,
                "city": city,
                "pages": len(words),
                **figures_,
                **boxes,
            }
        )
        people.extend({"key": key, **row} for row in rows)
        print(key, len(rows), [(row["name"], row["party"], row["votes"], row["result"]) for row in rows])
    pl.DataFrame(elections, infer_schema_length=None).write_parquet(PARSED / f"elections-v{VERSION}.parquet")
    pl.DataFrame(people, infer_schema_length=None).write_parquet(PARSED / f"candidates-v{VERSION}.parquet")


PUBLISHER = "総務省"
TITLE = "指定都市市長選挙 選挙結果調"
LICENSE = "Public Data License 1.0"
LICENSE_URL = "https://www.digital.go.jp/resources/open_data/public_data_license_v1.0"
ATTRIBUTION = "出典：総務省ホームページ"
RULES = {"formula": "fptp", "minimum": {"of": "valid", "share": 0.25}}
WIKIDATA = {"日本保守党": "Q122639399"}
MEASURES = {"registered": 2, "voters": 14, "cast": 3, "valid": 4, "invalid": 5}


def emit() -> Path:
    elections = pl.read_parquet(PARSED / f"elections-v{VERSION}.parquet").sort("held", "city")
    people = pl.read_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    with connect() as conn:
        prefectures = {
            iso: path
            for iso, path in conn.execute(
                "select p.iso_code, s.path from polity p join slug s on s.entity_id = p.id where p.iso_code like 'JP-__'"
            )
        }
        known = {
            (scheme, value)
            for scheme, value in conn.execute(
                "select scheme, value from external_id where scheme in ('jp.mic_chiji.party', 'jp.mic.party')"
            )
        }
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's designated-city mayoral elections 2011-2026 from the Ministry of Internal Affairs and "
            "Communications' election summaries, city totals",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str, kind: str = "official", preferred: bool | None = None) -> None:
            cs.add(
                "name",
                entity_id=entity,
                text=text,
                lang=lang,
                kind=kind,
                is_preferred=kind == "official" if preferred is None else preferred,
            )

        japan = cs.polity("JP")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=INDEX,
            license=LICENSE,
            license_url=LICENSE_URL,
            attribution=ATTRIBUTION,
            notes="Counts are as the ministry's summaries print them, read from each PDF's text layer. The summaries "
            "give city totals; the city election commissions publish the same counts by ward.",
        )
        cs.artifact(raw.cached(SOURCE, INDEX), source)
        system = cs.key(f"{SOURCE}.electoral_system", "mayor")
        cs.add("electoral_system", id=system, polity_id=japan, rules=RULES, rules_version=1)
        name(system, "First past the post with a legal minimum of a quarter of the valid votes", "en")
        name(system, "相対多数制（法定得票数は有効投票総数の4分の1）", "ja", "native", False)

        def party_of(text: str | None, held: date) -> UUID | None:
            if text in WIKIDATA:
                return cs.key("wikidata", WIKIDATA[text])
            found = None if text is None else party_key(text, held.isoformat())
            if found is None:
                return None
            if found[0] == "wikidata":
                return cs.key(*found)
            for scheme in ("jp.mic_chiji.party", "jp.mic.party"):
                if (scheme, found[1]) in known:
                    return cs.key(scheme, found[1])
            entity = cs.key(f"{SOURCE}.party", found[1])
            if entity in cs.minted and entity not in added:
                added.add(entity)
                cs.add("party", id=entity, kind="party", polity_id=japan)
                name(entity, found[1], "ja")
            return entity

        added: set[UUID] = set()
        cities: dict[str, tuple[UUID, UUID, UUID, UUID, str]] = {}
        for city in elections["city"].unique().sort():
            code, english = CITIES[city]
            polity = cs.key("jp.polity", city)
            prefecture = cs.polity(f"JP-{code[:2]}")
            area = cs.area("jp.municipality", code)
            prefix = f"{prefectures[f'JP-{code[:2]}']}/{english.lower()}-city"
            if polity in cs.minted:
                cs.add("polity", id=polity, kind="municipality", iso_code=None)
                name(polity, english, "en")
                name(polity, city, "ja", "native", False)
                cs.add("polity_parent", polity_id=polity, parent_id=prefecture)
                cs.add("polity_territory", polity_id=polity, area_id=area)
                cs.add("slug", path=prefix, entity_id=polity)
            office = cs.key("jp.office", f"mayor-{code}")
            if office in cs.minted:
                cs.add("office", id=office, polity_id=polity, kind="mayor")
                name(office, f"Mayor of {english}", "en")
                name(office, f"{city}長", "ja", "native", False)
            unit = cs.unit("jp.municipality", code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=japan,
                    scheme_code="jp.municipality",
                    code=code,
                    parent_id=cs.unit("jp.prefecture", code[:2]),
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            cities[city] = (polity, office, area, unit, prefix)

        locators: list[dict[str, object]] = []
        for row in elections.iter_rows(named=True):
            held = date.fromisoformat(row["held"])
            city = row["city"]
            english = CITIES[city][1]
            polity, office, area, unit, prefix = cities[city]
            artifact = cs.artifact(raw.cached(SOURCE, row["url"]), source)
            image = PAGES / f"{row['sha256']}-1.png"
            width, height = Image.open(image).size
            page_id = cs.page(artifact, 1)
            cs.add(
                "source_page",
                id=page_id,
                artifact_id=artifact,
                page_number=1,
                image_key=f"pages/{SOURCE}/{image.name}",
                width=width,
                height=height,
            )
            election = cs.key(f"{SOURCE}.election", row["key"])
            cs.add(
                "election", id=election, polity_id=polity, kind="general", date_from=held, date_to=held, status="held"
            )
            name(election, f"{held.year} {english} mayoral election", "en")
            name(election, f"{city}長選挙", "ja", "native", False)
            path = f"{prefix}/{held}-mayor"
            cs.add("slug", path=path, entity_id=election)
            contest = cs.key(f"{SOURCE}.contest", row["key"])
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                magnitude=1,
                round_number=1,
                electoral_system_id=system,
                default_result_kind="counted",
            )
            name(contest, f"{held.year} election for Mayor of {english}", "en")
            cs.add("slug", path=f"{path}/mayor", entity_id=contest)
            result_set = cs.key(f"{SOURCE}.result_set", row["key"])
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=source,
                status="active",
                is_default=True,
                completeness=1,
            )

            def value(option: UUID | None, measure: int, amount: str, box: list[float] | None) -> None:
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=Decimal(amount),
                )
                if box is not None:
                    locators.append(
                        {
                            "result_set_id": result_set,
                            "unit_id": unit,
                            "option_id": option,
                            "channel": 0,
                            "measure": measure,
                            "count_round": 0,
                            "page_id": page_id,
                            "x0": box[0],
                            "y0": box[1],
                            "x1": box[2],
                            "y1": box[3],
                        }
                    )

            for key, measure in MEASURES.items():
                if row[key] is not None:
                    value(None, measure, row[key], row[f"{key}_box"])
            for candidate in people.filter(pl.col("key") == row["key"]).sort("order").iter_rows(named=True):
                legal = candidate["legal_name"] or candidate["name"]
                person = cs.key(f"{SOURCE}.person", f"{CITIES[city][0]}:{legal.replace(' ', '')}")
                if person in cs.minted and person not in added:
                    added.add(person)
                    cs.add("person", id=person, gender={"男": "male", "女": "female"}.get(candidate["sex"] or ""))
                    name(person, legal, "ja")
                party = party_of(candidate["party"], held)
                option = cs.key(f"{SOURCE}.option", f"{row['key']}:{candidate['order']}")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party,
                    ballot_position=candidate["order"],
                    ballot_label=candidate["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{row['key']}:{candidate['order']}"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=candidate["name"],
                    member_of_party_id=party,
                    is_independent=party is None,
                    is_incumbent=candidate["status"] == "現",
                )
                value(option, 1, candidate["votes"], candidate["box"])
                if candidate["result"] == "当":
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
        for locator in locators:
            cs.add("value_locator", **locator)
        return cs.write()
