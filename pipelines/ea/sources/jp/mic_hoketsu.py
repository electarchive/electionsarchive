import itertools
import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.jp import mic, mic_hoc
from ea.sources.jp.mlit import PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic_hoketsu"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ROOT = f"{mic.DATA_ROOT}/hoketsu"
PAGES = {"hor": f"{ROOT}/hoketsu_1.html", "hoc": f"{ROOT}/hoketsu_2.html"}
TITLE = "衆議院議員・参議院議員補欠選挙結果調"
SINCE = "2009-08-31"
LINK = re.compile(r'<a [^>]*href="([^"]+\.pdf)"[^>]*>(.*?)</a>', re.DOTALL)
WORD = re.compile(
    r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>',
)
NUMBER = re.compile(r"^[\d,]+(?:\.\d+)?$")
KANA = re.compile(r"^[぀-ゟ\s]+$")
ERAS = {"令和": 2018, "平成": 1988, "昭和": 1925}
NAMES = dict(PREFECTURES)
CODES = {native: f"{number:02d}" for number, (native, _) in enumerate(PREFECTURES, 1)}
ALIASES = {"社民党": "社会民主党", "自民党": "自由民主党", "日本維新": "日本維新の会", "公明": "公明党"}


def era_date(text: str) -> str:
    text = unicodedata.normalize("NFKC", text)
    match = re.search(r"(令和|平成|昭和)\s*(元|\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日", text)
    if match is None:
        raise ValueError(text)
    year = ERAS[match[1]] + (1 if match[2] == "元" else int(match[2]))
    return date(year, int(match[3]), int(match[4])).isoformat()


@dataclass(frozen=True)
class Listing:
    chamber: str
    url: str
    label: str


def listings() -> list[Listing]:
    found = []
    for chamber, page in PAGES.items():
        text = raw.cached(SOURCE, page).path.read_bytes().decode("cp932", errors="replace")
        for href, label in LINK.findall(text):
            label = unicodedata.normalize("NFKC", re.sub(r"<[^>]+>|&nbsp;", "", label)).replace(" ", "")
            url = href if href.startswith("http") else f"{mic.ROOT}{href}" if href.startswith("/") else f"{ROOT}/{href}"
            found.append(Listing(chamber, url, label))
    return found


def wanted() -> list[Listing]:
    return [item for item in listings() if era_date(item.label) >= SINCE]


def fetch() -> None:
    for page in PAGES.values():
        raw.fetch(SOURCE, page, suffix=".html")
    for item in listings():
        raw.fetch(SOURCE, item.url, suffix=".pdf")


def words(path: Path) -> list[tuple[float, float, float, float, str]]:
    xml = subprocess.run(["pdftotext", "-bbox", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return [
        (float(a), float(b), float(c), float(d), unicodedata.normalize("NFKC", t))
        for a, b, c, d, t in WORD.findall(xml)
    ]


def layout(path: Path) -> str:
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout


def rows_of(items: list[tuple[float, float, float, float, str]], joiner: str = "") -> list[str]:
    rows: list[list[tuple[float, float, str]]] = []
    for x, y, _, _, text in sorted(items, key=lambda item: (item[1], item[0])):
        if rows and abs(rows[-1][0][1] - y) < 3:
            rows[-1].append((x, y, text))
        else:
            rows.append([(x, y, text)])
    return [joiner.join(text for _, _, text in sorted(row)) for row in rows]


def totals(text: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for key, label in {
        "valid": "有効投票数",
        "invalid": "無効投票数",
        "cast": "投票総数",
        "other": "持帰りその他",
        "threshold": "法定得票数",
        "forfeit": "供託物没収点",
    }.items():
        spaced = r"[ ]*".join(re.escape(ch) for ch in label)
        match = re.search(spaced + r"\s*([\d,]+(?:\.\d+)?)\s*票", text)
        if match:
            out[key] = match[1].replace(",", "")
    for key, label in {"registered": "当日有権者数", "voters": "投票者数"}.items():
        spaced = r"[ ]*".join(re.escape(ch) for ch in label)
        match = re.search(spaced + r"\s*([\d,]+)\s*人\s*([\d,]+)\s*人\s*([\d,]+)\s*人", text)
        if match:
            out[key] = match[3].replace(",", "")
            out[f"{key}_men"] = match[1].replace(",", "")
            out[f"{key}_women"] = match[2].replace(",", "")
    return out


def person_rows(rows: list[str]) -> tuple[str, str | None]:
    legal = next((row for row in rows if row.startswith("(")), None)
    plain = [row for row in rows if not row.startswith("(") and not KANA.match(row)]
    if not plain:
        plain = [row for row in rows if not row.startswith("(")][-1:]
    ballot = plain[0] if plain else ""
    return ballot, legal.strip("()") if legal else None


def parse_pdf(item: Listing) -> tuple[dict, list[dict]]:
    path = raw.cached(SOURCE, item.url).path
    items = words(path)
    text = layout(path)
    header = next(w for w in items if w[4] == "年齢")
    top = header[1]
    band = [w for w in items if top - 25 <= w[1] <= top + 30]
    age_x = min(w[0] for w in band if w[4] in {"性別", "年齢"})
    occupation = [w for w in band if "職" in w[4] or "業" in w[4]]
    occ_right = max(w[2] for w in occupation)
    status = next(w for w in band if w[4].startswith("新現"))
    party_left = min(w[0] for w in band if w[4] in {"届出政党", "党", "派", "(所属団体)"} and w[0] > occ_right)
    outcome_x = next(w[0] for w in band if w[4] == "当落") - 8
    footer = min(
        (w[1] for w in items if w[1] > top + 30 and (w[4].startswith("「届出") or w[4] == "投")), default=10_000
    )
    anchors = sorted(
        (
            w
            for w in items
            if top + 30 < w[1] < footer
            and NUMBER.match(w[4])
            and status[0] <= w[0] < outcome_x
            and any(o[4] in {"当", "落"} and o[0] >= outcome_x and abs(o[1] - w[1]) < 4 for o in items)
        ),
        key=lambda w: w[1],
    )
    if not anchors:
        raise ValueError(f"{item.label}: no candidate rows")
    cuts = [top + 20] + [(a[1] + b[1]) / 2 for a, b in itertools.pairwise(anchors)] + [footer]
    mid = (occ_right + party_left) / 2
    candidates = []
    for index, anchor in enumerate(anchors):
        cell = [w for w in items if cuts[index] <= w[1] + 1 < cuts[index + 1] and w[1] < footer]
        names = [w for w in cell if w[2] < age_x - 2]
        parties = [w for w in cell if mid <= w[0] and w[2] <= status[0] + 1]
        ages = [w for w in cell if age_x - 2 <= w[0] < occ_right - 40 and re.fullmatch(r"\d{2}", w[4])]
        tags = [w[4] for w in cell if status[0] - 2 <= w[0] < anchor[0] and w[4] in {"新", "現", "元", "前"}]
        result = [w[4] for w in cell if w[0] >= outcome_x and w[4] in {"当", "落"}]
        if len(result) != 1 or len(tags) != 1:
            raise ValueError(f"{item.label}: candidate {index}: tags {tags} result {result}")
        ballot, legal = person_rows(rows_of(names, " "))
        party = "".join(rows_of(parties))
        party = re.sub(r"^\((.*)\)$", r"\1", party)
        candidates.append(
            {
                "order": index + 1,
                "name": ballot,
                "legal_name": legal,
                "age": ages[0][4] if ages else None,
                "party": "" if party == "なし" else ALIASES.get(party, party),
                "status": tags[0],
                "votes": anchor[4].replace(",", ""),
                "result": result[0],
            }
        )
    held = era_date(
        next(line for line in text.splitlines() if "選挙期日" in re.sub(r"\s", "", line)).split("日", 1)[-1]
    )
    return {"held": held, "chamber": item.chamber, "url": item.url, "scanned": False, **totals(text)}, candidates


def district_of(item: Listing) -> tuple[list[str], int | None]:
    label = item.label
    prefectures = [native for native, _ in PREFECTURES if native in label.split("執行")[-1]]
    number = re.search(r"第(\d+)区", label)
    return prefectures, int(number[1]) if number else None


SCANNED = {
    "平成23年4月24日執行愛知県第6区選出議員補欠選挙": (
        {
            "held": "2011-04-24",
            "registered": "418455",
            "voters": "175520",
            "cast": "175515",
            "valid": "169779",
            "invalid": "5736",
            "other": "5",
            "threshold": "28296.500",
            "forfeit": "16977.900",
        },
        [
            ("福原 まゆみ", "福原 真由美", "幸福実現党", "新", "7932", "落"),
            ("かわえ 明美", "河江 明美", "日本共産党", "新", "14369", "落"),
            ("川村 まさよ", "川村 昌代", "減税日本", "新", "39308", "落"),
            ("丹羽 ひでき", "丹羽 秀樹", "自由民主党", "元", "104328", "当"),
            ("目片 文夫", None, "", "新", "3842", "落"),
        ],
    ),
    "平成22年10月24日執行北海道第5区選出議員補欠選挙": (
        {
            "held": "2010-10-24",
            "registered": "454271",
            "voters": "242932",
            "cast": "242929",
            "valid": "240376",
            "invalid": "2553",
            "other": "3",
            "threshold": "40062.666",
            "forfeit": "24037.600",
        },
        [
            ("中前 茂之", None, "民主党", "新", "94135", "落"),
            ("宮内 さとし", "宮内 聡", "日本共産党", "新", "15583", "落"),
            ("森山 佳則", None, "幸福実現党", "新", "2325", "落"),
            ("町村 信孝", None, "自由民主党", "前", "125636", "当"),
            ("河村 みちこ", "河村 美知子", "", "新", "2697", "落"),
        ],
    ),
}


def scanned(item: Listing) -> tuple[dict, list[dict]]:
    meta, people = SCANNED[item.label]
    rows = [
        {
            "order": order,
            "name": name,
            "legal_name": legal,
            "age": None,
            "party": party,
            "status": status,
            "votes": votes,
            "result": result,
        }
        for order, (name, legal, party, status, votes, result) in enumerate(people, 1)
    ]
    return {"chamber": item.chamber, "url": item.url, "scanned": True} | meta, rows


def parse() -> None:
    contests: list[dict] = []
    candidates: list[dict] = []
    for item in wanted():
        if not layout(raw.cached(SOURCE, item.url).path).strip():
            meta, rows = scanned(item)
        else:
            meta, rows = parse_pdf(item)
        prefectures, district = district_of(item)
        meta |= {
            "label": item.label,
            "prefectures": "・".join(prefectures),
            "district": district,
            "rerun": "再選挙" in item.label,
        }
        valid = Decimal(meta["valid"])
        total = sum(Decimal(row["votes"]) for row in rows)
        if abs(total - valid) > Decimal("0.01"):
            raise ValueError(f"{item.label}: candidates sum to {total}, valid {valid}")
        if sum(row["result"] == "当" for row in rows) != 1:
            raise ValueError(f"{item.label}: expected one winner")
        contests.append(meta)
        candidates += [row | {"held": meta["held"], "chamber": item.chamber, "label": item.label} for row in rows]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(contests).write_parquet(PARSED / f"contests-v{VERSION}.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / f"candidates-v{VERSION}.parquet")


NHK = "NHKと裁判してる党弁護士法72条違反で"
RENAMED = {
    "NHKから国民を守る党": NHK,
    "NHK受信料を支払わない方法を教える党": NHK,
    "NHK受信料を支払わない国民を守る党": NHK,
    "政治家女子48党": NHK,
}
METHOD = (
    "Candidate votes for the whole district, the voters on the day, and the ballot counts from the ministry's "
    "by-election result summary (補欠選挙結果調). The summary prints no figures by municipality; the counts by "
    "counting district are in the prefectural election commissions' records. Votes for candidates whose names other "
    "candidates share are apportioned (按分) and carry decimals. The candidates' votes were checked against the "
    "valid votes (有効投票数)."
)
SMD_SYSTEM_NOTE = "A by-election fills one vacant seat in the single-member district by a plurality of votes."
HOC_NOTE = (
    "A by-election for a House of Councillors district seat, held when a seat in the district falls vacant; the "
    "winner serves the remainder of the departed councillor's term."
)
RERUN_NOTE = "Held as a re-run election (再選挙) after the previous winner's election was voided, not as a by-election."


def party_text(text: str) -> str:
    text = RENAMED.get(text, text)
    return mic.party_label(mic_hoc.ALIASES.get(text, text))


def emit() -> Path:
    contests = pl.read_parquet(PARSED / f"contests-v{VERSION}.parquet")
    candidates = pl.read_parquet(PARSED / f"candidates-v{VERSION}.parquet")
    english = dict(PREFECTURES)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's House of Representatives and House of Councillors by-elections since 2009 from the "
            "Ministry of Internal Affairs and Communications: candidates, votes for the whole district, voters, "
            "and declared winners",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        houses = {
            "hor": cs.key("jp.body", "house-of-representatives"),
            "hoc": cs.key("jp.body", "house-of-councillors"),
        }
        systems = {
            "hor": cs.key(f"{mic.SOURCE}.electoral_system", "hor-smd"),
            "hoc": cs.key(f"{mic_hoc.SOURCE}.electoral_system", "hoc-sntv"),
        }
        if {japan, *houses.values(), *systems.values()} & cs.minted:
            raise ValueError("run jp.mlit, jp.diet, jp.mic, and jp.mic_hoc first")
        source = cs.source(mic.PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=mic.PUBLISHER,
            title=TITLE,
            url=PAGES["hor"],
            license=mic.LICENSE,
            license_url=mic.LICENSE_URL,
            attribution=mic.ATTRIBUTION,
        )
        for page in PAGES.values():
            cs.artifact(raw.cached(SOURCE, page), source)
        cs.add("scheme", code="jp.hoc_district", polity_id=japan, kind="electoral_district", rank=2, noun="district")

        keyed: dict[tuple[str, str], UUID] = {}
        added: set[UUID] = set()

        def party(text: str, held: str) -> UUID | None:
            found = mic.party_key(party_text(text), held)
            if found == (f"{mic.SOURCE}.party", "つばさの党"):
                found = ("jp.mic_chiji.party", "つばさの党")
            if found is None:
                return None
            if found not in keyed:
                entity = keyed[found] = cs.key(*found)
                if entity in cs.minted:
                    if found[0] == "wikidata":
                        raise ValueError(f"{found[1]} should already exist from jp.diet")
                    print(f"NEW PARTY {found[1]}")
                    cs.add("party", id=entity, kind="party", polity_id=japan)
                    name(entity, found[1], "ja")
            return keyed[found]

        def unit(scheme: str, code: str, parent: UUID | None, area: UUID | None, relation: str, polity: UUID) -> UUID:
            entity = cs.unit(scheme, code)
            if entity in cs.minted and entity not in added:
                added.add(entity)
                cs.add(
                    "reporting_unit",
                    id=entity,
                    polity_id=polity,
                    scheme_code=scheme,
                    code=code,
                    parent_id=parent,
                    area_relation=relation,
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
            return entity

        def prefecture_unit(code: str) -> UUID:
            return unit("jp.prefecture", code, None, cs.area("jp.prefecture", code), "equals", cs.polity(f"JP-{code}"))

        values: list[tuple[UUID, UUID, UUID | None, int, Decimal]] = []
        elections: dict[tuple[str, str], UUID] = {}
        for meta in contests.iter_rows(named=True):
            held, chamber = meta["held"], meta["chamber"]
            day = date.fromisoformat(held)
            same = contests.filter((pl.col("held") == held) & (pl.col("chamber") == chamber))
            house = houses[chamber]
            path = f"jp/{held}-{chamber}-by-election"
            if (held, chamber) not in elections:
                election = elections[held, chamber] = cs.key(f"{SOURCE}.election", f"{held}:{chamber}")
                plural = "s" if same.height > 1 else ""
                house_name = "House of Representatives" if chamber == "hor" else "House of Councillors"
                house_ja = "衆議院議員" if chamber == "hor" else "参議院議員"
                cs.add(
                    "election",
                    id=election,
                    polity_id=japan,
                    kind="by_election",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                name(election, f"{day.year} Japanese {house_name} by-election{plural} ({day:%B} {day.day})")
                name(election, f"{day.year}年{day.month}月{day.day}日執行 {house_ja}補欠選挙", "ja", "native", False)
                cs.add("slug", path=path, entity_id=election)
            election = elections[held, chamber]
            prefectures = meta["prefectures"].split("・")
            codes = [f"{[n for n, _ in PREFECTURES].index(p) + 1:02d}" for p in prefectures]
            district = meta["district"]
            key = f"{held}:{chamber}:{''.join(codes)}" + (f":{district}" if district else "")
            contest = cs.key(f"{SOURCE}.contest", key)
            if chamber == "hor":
                label = f"{english[prefectures[0]]} {district}"
                label_ja = f"{prefectures[0]}第{district}区"
                area = None
                district_unit = unit(
                    "jp.hor_smd",
                    f"{held}:{codes[0]}{district:02d}",
                    prefecture_unit(codes[0]),
                    None,
                    "unlocated",
                    cs.polity(f"JP-{codes[0]}"),
                )
                slug_name = f"{slug(english[prefectures[0]])}-{district}"
            else:
                label = " and ".join(english[p] for p in prefectures) + " district"
                label_ja = meta["prefectures"] + "選挙区"
                if len(prefectures) == 1:
                    area = cs.area("jp.prefecture", codes[0])
                    district_unit = prefecture_unit(codes[0])
                else:
                    area = None
                    district_unit = unit("jp.hoc_district", f"{held}:{''.join(codes)}", None, None, "unlocated", japan)
                slug_name = slug(" and ".join(english[p] for p in prefectures))
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=house,
                district_area_id=area,
                magnitude=1,
                electoral_system_id=systems[chamber],
                default_result_kind="counted",
            )
            name(contest, label + (" re-run" if meta["rerun"] else ""))
            name(contest, label_ja + ("再選挙" if meta["rerun"] else "補欠選挙"), "ja", "native", False)
            cs.add("slug", path=f"{path}/{slug_name}", entity_id=contest)
            cs.add(
                "note", entity_id=contest, kind="methodology", body_md=SMD_SYSTEM_NOTE if chamber == "hor" else HOC_NOTE
            )
            if meta["rerun"]:
                cs.add("note", entity_id=contest, kind="caveat", body_md=RERUN_NOTE)
            result_set = cs.key(f"{SOURCE}.result_set", key)
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
            cs.artifact(raw.cached(SOURCE, meta["url"]), source)
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD
                + f" The legal minimum for election (法定得票数) was {Decimal(meta['threshold']):,} votes and the "
                f"deposit forfeiture line (供託物没収点) {Decimal(meta['forfeit']):,}.",
            )
            if meta["scanned"]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md="The ministry's summary for this by-election is a scanned image with no text layer, so "
                    "its figures were read from page 1 by eye; the candidates' votes sum to the valid votes "
                    "(有効投票数), and voters equal ballots cast plus ballots taken home.",
                )
            seen: set[str] = set()
            for row in candidates.filter(pl.col("label") == meta["label"]).iter_rows(named=True):
                option = cs.key(f"{SOURCE}.option", f"{key}:{row['order']}")
                member = party(row["party"], held) if row["party"] else None
                plain_name = mic.plain(row["name"])
                suffix = f":{row['order']}" if plain_name in seen else ""
                seen.add(plain_name)
                if chamber == "hor":
                    person = cs.key(f"{mic.SOURCE}.person", f"{codes[0]}:{plain_name}{suffix}")
                else:
                    person = cs.key(f"{mic_hoc.SOURCE}.person", f"smd:{meta['prefectures']}:{plain_name}{suffix}")
                if person in cs.minted and person not in added:
                    added.add(person)
                    cs.add("person", id=person)
                    name(person, row["name"], "ja")
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=member,
                    ballot_position=row["order"],
                    ballot_label=row["name"],
                )
                cs.add(
                    "candidacy",
                    id=cs.key(f"{SOURCE}.candidacy", f"{key}:{row['order']}"),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=row["name"],
                    member_of_party_id=member,
                    is_independent=member is None,
                )
                elected = row["result"] == "当"
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind="elected" if elected else "lost",
                    seats=1 if elected else None,
                )
                values.append((result_set, district_unit, option, 1, Decimal(row["votes"])))
            for field, measure in {"registered": 2, "voters": 14, "cast": 3, "valid": 4, "invalid": 5}.items():
                values.append((result_set, district_unit, None, measure, Decimal(meta[field])))
        for result_set, place, option, measure, amount in values:
            cs.add(
                "result_value",
                result_set_id=result_set,
                unit_id=place,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=amount,
            )
        return cs.write()
