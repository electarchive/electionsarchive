import re
import subprocess
import unicodedata
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "jp.local_referendum"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OSAKA = "https://www.city.osaka.lg.jp/senkyo/cmsfiles/contents"
OKINAWA = "https://web.archive.org/web/{stamp}id_/https://www.pref.okinawa.lg.jp/site/chijiko/{path}"
NUMBER = re.compile(r"^-?[\d,]+$")
MEASURES = {"registered": 2, "cast": 3, "valid": 4, "invalid": 5, "voters": 14}


@dataclass(frozen=True)
class Commission:
    publisher: str
    title: str
    url: str
    license: str
    license_url: str
    attribution: str


@dataclass(frozen=True)
class Vote:
    held: date
    commission: Commission
    polity: str
    scheme: str
    question: str
    english: str
    type: str
    options: tuple[tuple[str, str], ...]
    votes: str
    registered: str
    totals: str | None
    method: str
    system: str


OSAKA_CITY = "大阪市"
OSAKA_RULES = (
    "Held under the Act on the Establishment of Special Wards in Metropolitan Areas (大都市地域における特別区の設置に"
    "関する法律, 2012), art. 7: the plan to replace the city with special wards takes effect only if the votes for "
    "it are a majority of the valid votes cast in the city. There is no turnout threshold."
)
OSAKA_METHOD = (
    "Votes for and against by ward from the final count (最終) in the city election commission's record of the vote "
    "(区別・時間別得票数及び開票結果), with registered voters from its table of electors on the day (当日有権者数). "
    "Ballots cast (投票総数) are the ballots found at the count; voters (投票者数) also count ballots taken home "
    "(持帰り) and ballots not accepted (不受理). The city totals were checked against the commission's table of votes "
    "for and against (賛成及び反対の得票数)."
)
OKINAWA_RULES = (
    "Held under the Okinawa Prefecture ordinance on the prefectural referendum on the landfill for the construction "
    "of a US military base at Henoko (辺野古米軍基地建設のための埋立ての賛否を問う県民投票条例, 2018), enacted on a "
    "residents' petition. Voters chose one of three answers. The result binds no one: the governor must respect it, "
    "and when the leading answer reaches a quarter of those eligible to vote, the governor notifies the prime minister "
    "and the president of the United States of it (art. 10)."
)
OKINAWA_METHOD = (
    "Votes for each answer by municipality from the prefecture's final count (開票結果, 開票確定), with those eligible "
    "to vote (投票資格者数) from its final turnout table (投票結果, 投票確定). Ballots cast (投票総数) are the ballots "
    "found at the count; voters (投票者数) also count ballots not accepted and the like (不受理等). Ginowan, Okinawa, "
    "Uruma, Miyakojima, and Ishigaki at first refused to hold the vote; after the ordinance was amended in January "
    "2019 to add the third answer, every municipality took part on the same day. The prefecture's pages were "
    "removed in its 2024 site move, so the files come from the Wayback Machine's captures."
)
OSAKA_COMMISSION = Commission(
    "大阪市選挙管理委員会",
    "特別区設置住民投票の結果しらべ（選挙の記録）",
    "https://www.city.osaka.lg.jp/senkyo/page/0000434623.html",
    "大阪市ホームページ著作権・免責 (free reuse, including commercial, with attribution; numeric data outside "
    "copyright)",
    "https://www.city.osaka.lg.jp/main/site_policy/0000000124.html",
    "出典：大阪市ホームページ",
)
OKINAWA_COMMISSION = Commission(
    "沖縄県",
    "県民投票投開票情報（辺野古米軍基地建設のための埋立ての賛否を問う県民投票）",
    "https://www.pref.okinawa.lg.jp/site/chijiko/henoko/kikaku/touhyoukekka.html",
    "Creative Commons Attribution 4.0 (沖縄県公開データ利用規約: site content is CC BY 4.0 unless noted)",
    "https://www.pref.okinawa.jp/_res/projects/default_project/_page_/001/016/461/20230222opendata_rule.pdf",
    "出典：沖縄県ホームページ",
)
VOTES = {
    "osaka-2015": Vote(
        date(2015, 5, 17),
        OSAKA_COMMISSION,
        "27100",
        "jp.ward",
        "大阪市における特別区の設置についての投票",
        "Osaka Metropolis plan referendum",
        "mandatory_referendum",
        (("yes", "賛成"), ("no", "反対")),
        f"{OSAKA}/0000434/434623/06(3).pdf",
        f"{OSAKA}/0000434/434623/05(3).pdf",
        f"{OSAKA}/0000434/434623/06(2).pdf",
        OSAKA_METHOD,
        OSAKA_RULES,
    ),
    "osaka-2020": Vote(
        date(2020, 11, 1),
        Commission(
            *(OSAKA_COMMISSION.publisher, OSAKA_COMMISSION.title),
            "https://www.city.osaka.lg.jp/senkyo/page/0000608618.html",
            *(OSAKA_COMMISSION.license, OSAKA_COMMISSION.license_url, OSAKA_COMMISSION.attribution),
        ),
        "27100",
        "jp.ward",
        "大阪市を廃止し特別区を設置することについての投票",
        "Osaka Metropolis plan referendum",
        "mandatory_referendum",
        (("yes", "賛成"), ("no", "反対")),
        f"{OSAKA}/0000608/608618/06(3).pdf",
        f"{OSAKA}/0000608/608618/05(03).pdf",
        f"{OSAKA}/0000608/608618/06(2).pdf",
        OSAKA_METHOD,
        OSAKA_RULES,
    ),
    "okinawa-2019": Vote(
        date(2019, 2, 24),
        OKINAWA_COMMISSION,
        "47",
        "jp.municipality",
        "普天間飛行場の代替施設として国が名護市辺野古に計画している米軍基地建設のための埋立てに対する賛否",
        "Henoko landfill referendum",
        "plebiscite",
        (("yes", "賛成"), ("no", "反対"), ("neither", "どちらでもない")),
        OKINAWA.format(stamp="20200213232440", path="henoko/kikaku/documents/kaihyou00.pdf"),
        OKINAWA.format(stamp="20190306043329", path="kentohyo/documents/touhyou00.pdf"),
        None,
        OKINAWA_METHOD,
        OKINAWA_RULES,
    ),
}
LABELS = {"yes": "For", "no": "Against", "neither": "Neither"}


def fetch() -> None:
    for vote in VOTES.values():
        for url in (vote.votes, vote.registered, vote.totals):
            if url is not None:
                raw.fetch(SOURCE, url, suffix=".pdf")


def lines(path: Path) -> list[str]:
    text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    return unicodedata.normalize("NFKC", text).splitlines()


def count(text: str) -> int:
    return int(text.replace(",", ""))


def split(line: str) -> tuple[str, list[int]] | None:
    words = line.split()
    numbers: list[int] = []
    while words and NUMBER.match(words[-1]):
        numbers.insert(0, count(words.pop()))
    label = "".join(words)
    return (label, numbers) if label and numbers else None


def osaka(vote: Vote) -> list[dict[str, object]]:
    text = lines(raw.cached(SOURCE, vote.votes).path)
    start = max(i for i, line in enumerate(text) if re.sub(r"\s", "", line) == "最終")
    rows: dict[str, dict[str, int]] = {}
    total: dict[str, int] = {}
    for line in text[start:]:
        line = re.sub(r"\s*(最終)?\d{1,2}:\d{2}\s*$", "", line)
        if (found := split(line)) is None:
            continue
        label, numbers = found
        if len(numbers) != 8:
            raise ValueError(f"{vote.held}: {line}")
        yes, no, valid, invalid, cast, home, rejected, voters = numbers
        if yes + no != valid or valid + invalid != cast or cast + home + rejected != voters:
            raise ValueError(f"{vote.held} {label} does not add up")
        figures = {"yes": yes, "no": no, "valid": valid, "invalid": invalid, "cast": cast, "voters": voters}
        if label == "計":
            total = figures
        elif label.endswith("区"):
            rows[label] = figures
    registered: dict[str, int] = {}
    for line in lines(raw.cached(SOURCE, vote.registered).path):
        if (found := split(line)) and len(found[1]) == 3:
            registered[found[0]] = found[1][2]
    if len(rows) != 24 or set(rows) - set(registered):
        raise ValueError(f"{vote.held}: {len(rows)} wards, {sorted(set(rows) - set(registered))} without electors")
    for key, value in total.items():
        if sum(row[key] for row in rows.values()) != value:
            raise ValueError(f"{vote.held}: wards do not sum to the city's {key}")
    if sum(registered[ward] for ward in rows) != registered["大阪市計"]:
        raise ValueError(f"{vote.held}: wards do not sum to the city's electors")
    assert vote.totals is not None
    summary = re.sub(r"[\s,]", "", "".join(lines(raw.cached(SOURCE, vote.totals).path)))
    if f"賛成{total['yes']}" not in summary or f"反対{total['no']}" not in summary:
        raise ValueError(f"{vote.held}: the totals table disagrees")
    return [
        {"place": ward, **figures, "registered": registered[ward], "neither": None} for ward, figures in rows.items()
    ]


def okinawa(vote: Vote) -> list[dict[str, object]]:
    pattern = re.compile(r"^\D?\s*(\d{1,2})\s*(\D+?)\s+(-?[\d,]+(?:\s+-?[\d,]+)+)\s*$")
    rows: dict[str, dict[str, int]] = {}
    total: dict[str, int] = {}
    for line in lines(raw.cached(SOURCE, vote.votes).path):
        if "県合計" in re.sub(r"\s", "", line):
            numbers = [count(word) for word in line.split() if NUMBER.match(word)]
            total = dict(zip(("yes", "no", "neither", "valid", "invalid", "cast", "rejected", "voters"), numbers))
            continue
        if not (found := pattern.match(line)):
            continue
        numbers = [count(word) for word in found.group(3).split()]
        if len(numbers) != 8:
            raise ValueError(f"{vote.held}: {line}")
        yes, no, neither, valid, invalid, cast, rejected, voters = numbers
        place = re.sub(r"\s", "", found.group(2))
        if yes + no + neither != valid or valid + invalid != cast or cast + rejected != voters:
            raise ValueError(f"{vote.held} {place} does not add up")
        rows[place] = {
            "yes": yes,
            "no": no,
            "neither": neither,
            "valid": valid,
            "invalid": invalid,
            "cast": cast,
            "voters": voters,
        }
    registered: dict[str, int] = {}
    for line in lines(raw.cached(SOURCE, vote.registered).path):
        if found := re.match(r"^\D?\s*\d{1,2}\s*(\D+?)\s+[\d,]+\s+[\d,]+\s+([\d,]+)\s", line):
            registered[re.sub(r"\s", "", found.group(1))] = count(found.group(2))
    if len(rows) != 41 or set(rows) != set(registered):
        raise ValueError(f"{vote.held}: {len(rows)} municipalities, {len(registered)} with electors")
    for key in ("yes", "no", "neither", "valid", "invalid", "cast", "voters"):
        if sum(row[key] for row in rows.values()) != total[key]:
            raise ValueError(f"{vote.held}: municipalities do not sum to the prefecture's {key}")
    return [{"place": place, **figures, "registered": registered[place]} for place, figures in rows.items()]


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows: list[dict[str, object]] = []
    for key, vote in VOTES.items():
        found = osaka(vote) if vote.scheme == "jp.ward" else okinawa(vote)
        for row in found:
            rows.append({"vote": key, **row})
        print(
            key,
            len(found),
            {answer: sum(int(str(row[answer] or 0)) for row in found) for answer, _ in vote.options},
        )
    pl.DataFrame(rows).write_parquet(PARSED / f"votes-v{VERSION}.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / f"votes-v{VERSION}.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Japan's local referendums: the Osaka Metropolis plan votes of 2015 and 2020 by ward and the "
            "Okinawa Henoko landfill vote of 2019 by municipality, from the Osaka City election commission and "
            "Okinawa Prefecture",
            polity="JP",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official", preferred: bool = True) -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=preferred)

        japan = cs.polity("JP")
        city = cs.key("jp.polity", OSAKA_CITY)
        if city in cs.minted:
            cs.add("polity", id=city, kind="municipality", iso_code=None)
            name(city, "Osaka")
            name(city, OSAKA_CITY, "ja", "native", False)
            cs.add("polity_parent", polity_id=city, parent_id=cs.polity("JP-27"))
            cs.add("polity_territory", polity_id=city, area_id=cs.area("jp.municipality", "27100"))
            cs.add("slug", path="jp/osaka/osaka-city", entity_id=city)
        system = cs.key(f"{SOURCE}.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=japan, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Local referendum, decided by the answer with the most valid votes")
        name(system, "住民投票", "ja", "native", False)
        sources: dict[str, UUID] = {}
        for vote in VOTES.values():
            commission = vote.commission
            source = sources.get(commission.url)
            if source is None:
                source = sources[commission.url] = cs.source(commission.publisher, f"{commission.title} {vote.held}")
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=commission.publisher,
                    title=f"{commission.title} {vote.held}",
                    url=commission.url,
                    license=commission.license,
                    license_url=commission.license_url,
                    attribution=commission.attribution,
                )
        values: list[tuple[UUID, UUID, UUID | None, int, int]] = []
        for key, vote in VOTES.items():
            rows = frame.filter(pl.col("vote") == key)
            source = sources[vote.commission.url]
            for url in (vote.votes, vote.registered, vote.totals):
                if url is not None:
                    cs.artifact(raw.cached(SOURCE, url), source)
            osaka_city = vote.scheme == "jp.ward"
            polity = city if osaka_city else cs.polity(f"JP-{vote.polity}")
            district = cs.area("jp.municipality", vote.polity) if osaka_city else cs.area("jp.prefecture", vote.polity)
            prefix = "jp/osaka/osaka-city" if osaka_city else "jp/okinawa"
            election = cs.key(f"{SOURCE}.election", key)
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                kind="referendum",
                date_from=vote.held,
                date_to=vote.held,
                status="held",
            )
            name(election, f"{vote.held.year} {vote.english}")
            name(election, vote.question, "ja", "native", False)
            cs.add("slug", path=f"{prefix}/{vote.held}-referendum", entity_id=election)
            contest = cs.key(f"{SOURCE}.contest", key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=district,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type=vote.type)
            name(contest, vote.english)
            name(contest, vote.question, "ja", "native", False)
            cs.add("slug", path=f"{prefix}/{vote.held}-referendum/question", entity_id=contest)
            cs.add("citation", entity_id=contest, source_id=source)
            cs.add(
                "ballot_question_text",
                id=uuid5(NAMESPACE_URL, f"{SOURCE}\x1fballot_question_text\x1f{contest}"),
                contest_id=contest,
                kind="text",
                lang="ja",
                body=vote.question,
                url=vote.commission.url,
                source_id=source,
            )
            result = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=vote.method)
            cs.add("note", entity_id=contest, kind="methodology", body_md=vote.system)
            totals = {answer: int(rows[answer].sum()) for answer, _ in vote.options}
            options: dict[str, UUID] = {}
            for order, (answer, label) in enumerate(vote.options, 1):
                options[answer] = cs.key(f"{SOURCE}.option", f"{key}:{answer}")
                cs.add(
                    "contest_option",
                    id=options[answer],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=order,
                    ballot_label=f"{LABELS[answer]} ({label})",
                )
                cs.add(
                    "outcome",
                    result_set_id=result,
                    option_id=options[answer],
                    kind="won" if totals[answer] == max(totals.values()) else "lost",
                )
            codes = {
                text: code
                for code, text in conn.execute(
                    """select c.code, n.text from area a join area_code c on c.area_id = a.id
                       join name n on n.entity_id = a.id and n.lang = 'ja' and n.kind = 'official'
                       where a.scheme_code = %s and c.code like %s and n.valid @> %s::date""",
                    [vote.scheme, f"{vote.polity[:3] if osaka_city else vote.polity}%", vote.held],
                )
            }
            parent = cs.unit("jp.prefecture", vote.polity[:2])
            for row in rows.iter_rows(named=True):
                if row["place"] not in codes:
                    raise ValueError(f"{key}: no unit named {row['place']} on {vote.held}")
                code = codes[row["place"]]
                unit = cs.unit(vote.scheme, code)
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=japan,
                        scheme_code=vote.scheme,
                        code=code,
                        parent_id=parent,
                        area_relation="equals",
                        area_id=cs.area(vote.scheme, code),
                        kind="standard",
                        valid_from=None,
                    )
                for answer, option in options.items():
                    values.append((result, unit, option, 1, row[answer]))
                for field, measure in MEASURES.items():
                    values.append((result, unit, None, measure, row[field]))
        cs.frame(
            "result_value",
            pl.DataFrame(
                [
                    (str(result), str(unit), None if option is None else str(option), 0, measure, 0, 0, value)
                    for result, unit, option, measure, value in values
                ],
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "channel": pl.Int16,
                    "measure": pl.Int16,
                    "count_round": pl.Int16,
                    "flags": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ),
        )
        return cs.write()
