import csv
import functools
import re
import struct
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import foreign_preferred
from ea.store import DATA, connect

SOURCE = "gb.lcc"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
ARCHIVE = "https://archive.org"
EDITIONS = {
    "londonmanual01unkngoog": (1901, 1898, range(37, 43)),
    "londonmanualfor00donagoog": (1905, 1904, range(40, 47)),
    "londonmanualfor01donagoog": (1907, 1907, range(40, 47)),
    "londonmanualfor02donagoog": (1908, 1907, range(39, 46)),
    "londonmanual00unkngoog": (1908, 1907, range(38, 45)),
}
DAYS = {1898: date(1898, 3, 3), 1904: date(1904, 3, 5), 1907: date(1907, 3, 2)}
Word = tuple[str, float, float, float, float]
VOTES, REGISTERED = 1, 2
DIGITIZED = 8
PUBLISHER = "Edward Lloyd"
TITLES = {1898: "The London Manual for 1901", 1904: "The London Manual for 1905", 1907: "The London Manual for 1907"}
PARTIES = {
    "P": "Progressive Party (London)",
    "M": "Moderate Party (London)",
    "ILP": "Independent Labour Party",
}
ILP = UUID("2ca85788-cbb1-5422-911e-a3e8f0c27c1b")
COUNTY = "county-of-london"
CREATED, ABOLISHED = date(1889, 4, 1), date(1965, 4, 1)
METHODOLOGY = (
    "Results by electoral division from The London Manual, an annual handbook of London government published "
    "by Edward Lloyd and edited by Robert Donald, as scanned by Google Books and held on the Internet Archive. "
    "The Manual prints each division with its electorate, every candidate with a party letter and votes, and an "
    "asterisk for sitting members; the members returned are printed flush left and the defeated candidates "
    "indented. The figures were transcribed by hand from the page images and checked against the Internet "
    "Archive's text recognition of the same pages and of the other editions that reprint them: 1907 is printed in "
    "three editions, and each later edition repeats the previous election's votes under every division. Party "
    "letters are stored as printed: P for the Progressives and M for the Moderates (named the Municipal Reform "
    "Party from 1906), ILP for the Independent Labour Party, and I for independents. The letters L and S are "
    "kept on the ballot label without a party link. Aldermen, chosen by the council, are not included."
)
NOTES = {
    (1904, "west-newington"): "One digit of H. C. Gibbons's vote is damaged in the 1905 edition; the 1907 edition's "
    "note on the 1904 election prints it as 1,356, which is the figure stored.",
}
PRIMARY = {1898: "londonmanual01unkngoog", 1904: "londonmanualfor00donagoog", 1907: "londonmanualfor01donagoog"}


def pdf(item: str) -> str:
    return f"{ARCHIVE}/download/{item}/{item}.pdf"


def djvu(item: str) -> str:
    return f"{ARCHIVE}/download/{item}/{item}_djvu.xml"


def fetch() -> None:
    for item in EDITIONS:
        raw.fetch(SOURCE, pdf(item), suffix=".pdf")
        raw.fetch(SOURCE, djvu(item), suffix=".xml")


@functools.cache
def objects(item: str) -> list[str]:
    return raw.cached(SOURCE, djvu(item)).path.read_text().split("<OBJECT")[1:]


def words(item: str, page: int) -> tuple[list[Word], int, int]:
    body = objects(item)[page - 1]
    width, height = (int(re.findall(rf'{side}="(\d+)"', body)[0]) for side in ("width", "height"))
    found = []
    for coords, text in re.findall(r'<WORD coords="([\d,]+)"[^>]*>([^<]*)</WORD>', body):
        left, bottom, right, top = (int(value) for value in coords.split(",")[:4])
        found.append(
            (text.replace("&amp;", "&").replace("&quot;", '"').replace("&apos;", "'"), left, top, right, bottom)
        )
    return found, width, height


def gutter(found: list[Word], width: int) -> float:
    covered = [0] * width
    for _, left, _, right, _ in found:
        for x in range(max(0, int(left)), min(width, int(right))):
            covered[x] += 1
    best, start = (0, width // 2), None
    for x in range(int(width * 0.35), int(width * 0.65)):
        if covered[x] == 0:
            start = x if start is None else start
            if x - start > best[0]:
                best = (x - start, (start + x) // 2)
        else:
            start = None
    return best[1]


def lines(found: list[Word]) -> list[list[Word]]:
    rows: list[list[Word]] = []
    for word in sorted(found, key=lambda w: (w[2] + w[4]) / 2):
        middle = (word[2] + word[4]) / 2
        if rows and abs(middle - sum((w[2] + w[4]) / 2 for w in rows[-1]) / len(rows[-1])) < 25:
            rows[-1].append(word)
        else:
            rows.append([word])
    return [sorted(row, key=lambda w: w[1]) for row in rows]


def columns(item: str, page: int) -> tuple[list[list[list[Word]]], int, int]:
    found, width, height = words(item, page)
    body = [w for w in found if w[2] > height * 0.08]
    split = gutter(body, width)
    return (
        [lines([w for w in body if (w[1] + w[3]) / 2 < split]), lines([w for w in body if (w[1] + w[3]) / 2 >= split])],
        width,
        height,
    )


def image(item: str, page: int, dpi: int = 300) -> Path:
    artifact = raw.cached(SOURCE, pdf(item))
    target = PAGES / f"{artifact.sha256}-{page}-{dpi}.png"
    if not target.exists():
        PAGES.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(artifact.path), str(target.with_suffix(""))],
            check=True,
        )
    return target


TRANSCRIBED = Path(__file__).with_name("lcc_transcribed.tsv")


def figure(text: str) -> int | None:
    cleaned = re.sub(r"[\s,.;:)(]", "", text)
    return int(cleaned) if cleaned.isdigit() else None


def stream(item: str) -> list[tuple[int, Word, int, int]]:
    found = []
    for page in EDITIONS[item][2]:
        cols, width, height = columns(item, page)
        for col in cols:
            for row in col:
                found.extend((page, word, width, height) for word in row)
    return found


def near(text: str, value: int) -> bool:
    printed = re.sub(r"[\s,.;:)(]", "", text)
    wanted = str(value)
    return len(printed) == len(wanted) and sum(a != b for a, b in zip(printed, wanted, strict=True)) == 1


def locate(words: list[tuple[int, Word, int, int]], start: int, value: int, reach: int) -> tuple[int, bool] | None:
    for index in range(start, min(len(words), start + reach)):
        if figure(words[index][1][0]) == value:
            return index, True
    for index in range(start, min(len(words), start + reach)):
        if near(words[index][1][0], value):
            return index, False
    return None


def parse() -> None:
    rows = list(csv.DictReader(TRANSCRIBED.open(), delimiter="\t"))
    candidates: list[dict] = []
    divisions: list[dict] = []
    for year in sorted({int(row["year"]) for row in rows}):
        listed = [row for row in rows if int(row["year"]) == year]
        order = list(dict.fromkeys(row["division"] for row in listed))
        reads: dict[tuple[str, int], list[tuple[str, int, list[float], bool]]] = {}
        blocks: dict[str, dict[str, tuple[int, int]]] = {}
        for item, (_, printed, _) in EDITIONS.items():
            if printed != year:
                continue
            words = stream(item)
            position = 0
            starts: dict[str, int] = {}
            for code in order:
                mine = [row for row in listed if row["division"] == code]
                hit = locate(words, position, int(mine[0]["electorate"]), 400)
                if hit is None:
                    continue
                position = hit[0] + 1
                starts[code] = hit[0]
                page, word, width, height = words[hit[0]]
                box = [word[1] / width, word[2] / height, word[3] / width, word[4] / height]
                reads.setdefault((code, 0), []).append((item, page, box, hit[1]))
                for number, row in enumerate(mine, 1):
                    if not row["votes"]:
                        continue
                    hit = locate(words, position, int(row["votes"]), 40)
                    if hit is None:
                        continue
                    position = hit[0] + 1
                    page, word, width, height = words[hit[0]]
                    box = [word[1] / width, word[2] / height, word[3] / width, word[4] / height]
                    reads.setdefault((code, number), []).append((item, page, box, hit[1]))
            ends = sorted(starts.values()) + [len(words)]
            blocks[item] = {code: (begin, next(e for e in ends if e > begin)) for code, begin in starts.items()}
        later = {}
        for item, (_, printed, _) in EDITIONS.items():
            if printed <= year:
                continue
            words = stream(item)
            following = [row for row in rows if int(row["year"]) == printed]
            position = 0
            starts = {}
            for code in dict.fromkeys(row["division"] for row in following):
                electorate = next(int(row["electorate"]) for row in following if row["division"] == code)
                hit = locate(words, position, electorate, 400)
                if hit is not None and hit[1]:
                    starts[code], position = hit[0], hit[0] + 1
            ends = sorted(starts.values()) + [len(words)]
            for code, begin in starts.items():
                stop = next(e for e in ends if e > begin)
                later.setdefault(code, []).append([figure(w[1][0]) for w in words[begin:stop]])
        for code in order:
            mine = [row for row in listed if row["division"] == code]
            seen = reads.get((code, 0), [])
            primary = next((r for r in seen if r[0] == PRIMARY[year]), None)
            divisions.append(
                {
                    "year": year,
                    "division": code,
                    "electorate": int(mine[0]["electorate"]),
                    "item": PRIMARY[year],
                    "page": primary[1] if primary else None,
                    "box": primary[2] if primary else None,
                    "agree": sum(r[3] for r in seen),
                }
            )
            for number, row in enumerate(mine, 1):
                seen = reads.get((code, number), [])
                primary = next((r for r in seen if r[0] == PRIMARY[year]), None)
                votes = int(row["votes"]) if row["votes"] else None
                candidates.append(
                    {
                        "year": year,
                        "division": code,
                        "number": number,
                        "name": row["name"],
                        "party": row["party"] or None,
                        "votes": votes,
                        "elected": row["elected"] == "1",
                        "item": PRIMARY[year],
                        "page": primary[1] if primary else None,
                        "box": primary[2] if primary else None,
                        "agree": sum(r[3] for r in seen),
                        "noted": sum(votes in block for block in later.get(code, [])) if votes else 0,
                    }
                )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(divisions).write_parquet(PARSED / "divisions.parquet")
    pl.DataFrame(candidates).write_parquet(PARSED / "candidates.parquet")
    frame = pl.DataFrame(candidates)
    print(
        frame.group_by("year").agg(
            pl.len(),
            (pl.col("agree") > 0).sum().alias("ocr"),
            (pl.col("noted") > 0).sum().alias("noted"),
            pl.col("box").is_null().sum().alias("nobox"),
        )
    )


def person_name(printed: str) -> str:
    return re.sub(r",? (M\.P\.|J\.P\.|\(Ald\.\))", "", printed).strip()


def emit() -> Path:
    divisions = pl.read_parquet(PARSED / "divisions.parquet")
    standing = pl.read_parquet(PARSED / "candidates.parquet")
    if divisions.height != 58 * len(DAYS):
        raise ValueError(f"run ea parse {SOURCE}")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="London County Council elections of 1898, 1904, and 1907 by electoral division, from The London "
            "Manual",
            polity="GB",
        )
        outranked = foreign_preferred(conn, cs, SOURCE)

        def name(entity: UUID, text: str) -> None:
            if entity in outranked and entity not in cs.minted:
                return
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def caveat(entity: UUID, text: str) -> None:
            cs.add("note", entity_id=entity, kind="caveat", body_md=text)

        gb = cs.polity("GB")
        england = cs.polity("GB-ENG")
        block = cs.key("gb.electoral_system", "block")
        if block in cs.minted:
            raise ValueError("publish gb.parliament first")
        area = cs.area("gb.lad", COUNTY)
        if area in cs.minted:
            cs.add("area", id=area, scheme_code="gb.lad")
            cs.add("area_code", area_id=area, code=COUNTY, valid_from=CREATED, valid_to=ABOLISHED)
            cs.add("area_parent", area_id=area, parent_id=cs.area("gb.country", "ENG"), valid_from=None, valid_to=None)
        name(area, "County of London")
        polity = cs.key("gb.polity", COUNTY)
        if polity in cs.minted:
            cs.add("polity", id=polity, kind="region", iso_code=None)
            cs.add("polity_parent", polity_id=polity, parent_id=england)
            cs.add("polity_territory", polity_id=polity, area_id=area)
        name(polity, "County of London")
        cs.add("slug", path=f"gb/{COUNTY}", entity_id=polity)
        council = cs.key("gb.body", "london-county-council")
        if council in cs.minted:
            cs.add("body", id=council, polity_id=polity, kind="council")
        name(council, "London County Council")
        parties: dict[str, UUID] = {"ILP": ILP}
        for code in ("P", "M"):
            parties[code] = cs.key("gb.party", slug(PARTIES[code]))
            if parties[code] in cs.minted:
                cs.add("party", id=parties[code], kind="party", polity_id=gb)
            name(parties[code], PARTIES[code])
        others = {
            contest
            for (contest,) in conn.execute(
                "select rs.contest_id from result_set rs join changeset c on c.id = rs.changeset_id "
                "where c.pipeline <> %s and rs.is_default",
                [SOURCE],
            ).fetchall()
        }
        displays = dict(
            conn.execute(
                "select c.code, n.text from area_code c join name n on n.entity_id = c.area_id and n.is_preferred"
                " where c.code like '1885:eng-%%'"
            ).fetchall()
        )
        units: set[UUID] = set()
        for year, day in DAYS.items():
            item = PRIMARY[year]
            title = TITLES[year]
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="compendium",
                    publisher=PUBLISHER,
                    title=title,
                    url=f"{ARCHIVE}/details/{item}",
                    license="Public domain; published in London in "
                    f"{EDITIONS[item][0]} and digitized by Google Books from a library copy",
                    license_url=None,
                    attribution=f"{PUBLISHER}, {title}, edited by Robert Donald (Internet Archive, {item}).",
                )
            artifact = raw.cached(SOURCE, pdf(item))
            artifact_id = cs.artifact(artifact, source)

            @functools.cache
            def page(number: int, artifact: raw.Artifact = artifact, artifact_id: UUID = artifact_id) -> UUID:
                image = PAGES / f"{artifact.sha256}-{number}.png"
                if not image.exists():
                    PAGES.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", "150", "-gray", "-png", "-f", str(number), "-l", str(number), "-singlefile"]
                        + [str(artifact.path), str(image.with_suffix(""))],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                found = cs.page(artifact_id, number)
                cs.add(
                    "source_page",
                    id=found,
                    artifact_id=artifact_id,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{image.name}",
                    width=width,
                    height=height,
                )
                return found

            election = cs.key("gb.election", f"{day}:lcc")
            cs.add("election", id=election, polity_id=polity, kind="general", date_from=day, date_to=day, status="held")
            name(election, f"{year} London County Council election")
            path = f"gb/{COUNTY}/{day}-lcc"
            cs.add("slug", path=path, entity_id=election)
            cs.add("note", entity_id=election, kind="methodology", body_md=METHODOLOGY)
            for division in divisions.filter(pl.col("year") == year).iter_rows(named=True):
                code = f"1885:eng-{division['division']}"
                district = cs.area("gb.pcon", code)
                if district in cs.minted:
                    raise ValueError(f"{code}: no 1885 constituency area")
                display = displays[code]
                unit = cs.unit("gb.pcon", code)
                if unit in cs.minted and unit not in units:
                    units.add(unit)
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=gb,
                        scheme_code="gb.pcon",
                        code=code,
                        area_relation="equals",
                        area_id=district,
                        kind="standard",
                        valid_from=None,
                    )
                lines = standing.filter((pl.col("year") == year) & (pl.col("division") == division["division"]))
                tacit = lines["votes"].is_null().all()
                seats = int(lines["elected"].sum())
                contest_key = f"{day}:lcc:{division['division']}"
                contest = cs.key("gb.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=council,
                    district_area_id=district,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=block,
                    default_result_kind="tacit" if tacit else "counted",
                )
                name(contest, f"{display}, London County Council, {year}")
                cs.add("slug", path=f"{path}/{slug(display)}", entity_id=contest)
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="tacit" if tacit else "counted",
                    status="active",
                    source_id=source,
                    is_default=contest not in others,
                    completeness=1,
                )
                locator: dict[str, object] = {"division": display}
                if division["page"] is not None:
                    locator["page"] = division["page"]
                cs.add("citation", entity_id=result_set, source_id=source, locator=locator)
                if (year, division["division"]) in NOTES:
                    caveat(result_set, NOTES[year, division["division"]])
                if not tacit:
                    won = [line["votes"] for line in lines.iter_rows(named=True) if line["elected"]]
                    lost = [line["votes"] for line in lines.iter_rows(named=True) if not line["elected"]]
                    if lost and min(won) < max(lost):
                        caveat(
                            result_set,
                            "The Manual prints a defeated candidate with more votes than a member it shows as "
                            "returned; both the votes and the members are stored as printed.",
                        )

                def value(option: UUID | None, measure: int, figure: int, number: int | None, box: list | None) -> None:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=DIGITIZED,
                        value=figure,
                    )
                    if number is None or box is None:
                        return
                    x0, y0, x1, y1 = box
                    cs.add(
                        "value_locator",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        page_id=page(number),
                        source_id=None,
                        x0=x0,
                        y0=y0,
                        x1=x1,
                        y1=y1,
                    )

                value(None, REGISTERED, division["electorate"], division["page"], division["box"])
                for line in lines.iter_rows(named=True):
                    option_key = f"{contest_key}:{line['number']}"
                    letter = line["party"]
                    owner = parties.get(letter) if letter else None
                    if letter in ("L", "S"):
                        caveat(result_set, f"{line['name']} is printed as ({letter}); no party is linked.")
                    elif letter is None:
                        caveat(result_set, f"The Manual prints no party for {line['name']}.")
                    option = cs.key(f"{SOURCE}.option", option_key)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=owner,
                        ballot_label=line["name"] + (f" ({letter})" if letter else ""),
                    )
                    person = cs.key(f"{SOURCE}.person", option_key)
                    cs.add("person", id=person)
                    name(person, person_name(line["name"]))
                    cs.add(
                        "candidacy",
                        id=cs.key(f"{SOURCE}.candidacy", option_key),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=line["name"],
                        member_of_party_id=owner,
                        is_independent=letter == "I",
                    )
                    if line["votes"] is not None:
                        value(option, VOTES, line["votes"], line["page"], line["box"])
                    if line["elected"]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        return cs.write()
