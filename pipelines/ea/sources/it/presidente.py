import html
import re
import struct
import subprocess
from bisect import bisect_right
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "it.presidente"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
PAGE = "https://leg14.camera.it/_presidenti/{}_scrutinio.asp"
RECORD = (
    "https://documenti.camera.it/leg17/resoconti/sedutaComune/pdf/sed{0:04d}/leg.17.sedutaComune.sed{0:04d}.data{1}.pdf"
)
HEADER = 95
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
MONTHS = {
    name: number
    for number, name in enumerate(
        [
            "gennaio",
            "febbraio",
            "marzo",
            "aprile",
            "maggio",
            "giugno",
            "luglio",
            "agosto",
            "settembre",
            "ottobre",
            "novembre",
            "dicembre",
        ],
        1,
    )
}
DATE = re.compile(r"(\d{1,2})[°º]? (" + "|".join(MONTHS) + r") ((?:19|20)\d\d)", re.IGNORECASE)
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5, "VI": 6, "VII": 7, "VIII": 8, "IX": 9, "X": 10}
HEAD = re.compile(
    r"(?:(?P<date>\d{1,2}[°º]? (?:" + "|".join(MONTHS) + r") (?:19|20)\d\d)\s+)?"
    r"(?:(?P<n>\d+\s*[°º])\s+scruti(?:nio|no)\s+|(?P<r>[IVX]+)\s+scrutinio\s+(?=Componenti|Presenti))",
    re.IGNORECASE,
)
PRESENT, VOTERS, BLANK, INVALID, VOTES = 20, 14, 6, 5, 1
DIGITIZED = 8
LICENSE = "No license stated. Official acts of the State are excluded from copyright (art. 5 of Law 633/1941)."


@dataclass(frozen=True)
class Item:
    year: int
    url: str
    person: tuple[str, str]
    days: tuple[date, ...] = ()


def page(name: str, year: int, wikidata: str) -> Item:
    return Item(year, PAGE.format(name), ("wikidata", wikidata))


ELECTIONS = [
    page("einaudi", 1948, "Q1242"),
    page("gronchi", 1955, "Q1241"),
    page("segni", 1962, "Q1240"),
    page("saragat", 1964, "Q1238"),
    page("leone", 1971, "Q1235"),
    page("pertini", 1978, "Q1233"),
    page("cossiga", 1985, "Q1230"),
    page("scalfaro", 1992, "Q1228"),
    page("ciampi", 1999, "Q1224"),
    page("napolitano", 2006, "Q1220"),
    Item(
        2013,
        RECORD.format(1, "20130418"),
        ("wikidata", "Q1220"),
        tuple(date(2013, 4, day) for day in (18, 18, 19, 19, 20, 20)),
    ),
    Item(
        2015,
        RECORD.format(23, "20150129"),
        ("it.person", "sergio-mattarella"),
        tuple(date(2015, 1, day) for day in (29, 30, 30, 31)),
    ),
]
SPECIAL = re.compile(r"(Voti\s+dispersi|Schede\s+bianche|Schede\s+nulle)", re.IGNORECASE)


def fetch() -> None:
    for item in ELECTIONS:
        raw.fetch(SOURCE, item.url, suffix=".pdf" if item.days else ".html")


def blank(text: str) -> str:
    previous = None
    while previous != text:
        previous = text
        text = re.sub(r"\([^()]*\)", lambda match: " " * len(match[0]), text)
    return text


def count(pattern: str, text: str, flags: int = re.IGNORECASE) -> re.Match[str] | None:
    return re.search(pattern + r"[\s.…:]*?(\d+)", text, flags)


def tally(block: str, base: int) -> dict[str, object]:
    found = {
        "present": count(r"Presenti(?: e e?votanti)?", block),
        "voters": count(r"Votanti", block),
        "required": count(r"Maggioranza[^\d]*?", block),
        "dispersed": count(r"Voti\s+dispersi", block),
        "blank": count(r"Schede\s+bianche", block),
        "invalid": count(r"Schede\s+nulle", block),
    }
    if found["present"] is None or found["required"] is None:
        raise ValueError(f"no counts in {block[:200]!r}")
    if found["voters"] is None:
        found["voters"] = found["present"]
    named = re.search(r"(?:Ottengono|Hanno ottenuto) voti\s*[:;]?", block, re.IGNORECASE)
    if named is None:
        raise ValueError(f"no names in {block[:200]!r}")
    stop = SPECIAL.search(block, named.end())
    names = block[named.end() : stop.start() if stop else len(block)]
    entries = [
        (re.sub(r"\s+", " ", match[1].replace("- ", "")).strip(" ;,.:"), base + named.end() + match.start(2), match[2])
        for match in re.finditer(r"([^\d]+?)\s*(\d+)\s*(?:[;,.]|(?=\s))", names)
    ]
    labels = [label for label, _, _ in entries]
    if len(set(labels)) != len(labels) or "" in labels:
        raise ValueError(f"names {labels}")
    return {
        "found": {key: (match.start(1) + base, match[1]) for key, match in found.items() if match is not None},
        "entries": entries,
    }


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    PAGES.mkdir(parents=True, exist_ok=True)
    ballots: list[dict[str, object]] = []
    values: list[dict[str, object]] = []
    pages: list[dict[str, object]] = []
    elected: list[dict[str, object]] = []
    for item in ELECTIONS:
        year = item.year
        artifact = raw.cached(SOURCE, item.url)
        blocks: list[tuple[int, date, str, int]] = []
        if item.days:
            words: list[tuple[int, float, float, float, float, str]] = []
            for number, chunk in enumerate(
                subprocess.run(
                    ["pdftotext", "-bbox", str(artifact.path), "-"], check=True, capture_output=True, text=True
                ).stdout.split("<page ")[1:],
                start=1,
            ):
                size = re.match(r'width="([\d.]+)" height="([\d.]+)"', chunk)
                if size is None:
                    raise ValueError(f"page {number} has no size")
                width, height = float(size[1]), float(size[2])
                words.extend(
                    (
                        number,
                        float(x0) / width,
                        float(y0) / height,
                        float(x1) / width,
                        float(y1) / height,
                        html.unescape(text),
                    )
                    for x0, y0, x1, y1, text in WORD.findall(chunk)
                    if float(y0) > HEADER
                )
            starts: list[int] = []
            offset = 0
            for word in words:
                starts.append(offset)
                offset += len(word[5]) + 1
            flat = " ".join(word[5] for word in words)
            found = list(re.finditer(r"Comunico il risultato della votazione", flat))
            days = item.days
            if len(found) != len(days):
                raise ValueError(f"{year}: {len(found)} results for {len(days)} ballots")
            for number, (match, day) in enumerate(zip(found, days, strict=True), 1):
                end = re.compile(r"Non essendo stata|Proclamo|Nessun candidato").search(flat, match.end())
                blocks.append((number, day, flat[match.end() : end.start() if end else None], match.end()))
            proclaimed = re.search(r"Proclamo eletto Presidente della Re?-? ?pubblica (\S+ \S+)", flat)
            if proclaimed is None:
                raise ValueError(f"{year}: no proclamation")
            elected_text = proclaimed[1]
        else:
            flat = html.unescape(artifact.path.read_bytes().decode("cp1252", "replace")).replace("\x92", "'")
            flat = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", flat.replace("\xa0", " ")))
            heads = list(HEAD.finditer(flat))
            day = None
            for index, head in enumerate(heads):
                if head["date"]:
                    parts = DATE.fullmatch(head["date"])
                    assert parts is not None
                    day = date(int(parts[3]), MONTHS[parts[2].lower()], int(parts[1]))
                label = (head["n"] or head["r"]).rstrip("°º ").strip()
                number = int(label) if label.isdigit() else ROMAN[label.upper()]
                end = heads[index + 1].start() if index + 1 < len(heads) else len(flat)
                if day is None or (blocks and number <= blocks[-1][0]):
                    raise ValueError(f"{year}: ballot {number} at position {index + 1}, day {day}")
                blocks.append((number, day, flat[head.end() : end], head.end()))
            elected_text = flat[flat.lower().rfind("eletto presidente della repubblica") :][:80]
        if not blocks:
            raise ValueError(f"{year}: no ballots")
        top: tuple[int, str] = (0, "")
        for number, day, block, base in blocks:
            parsed = tally(blank(block), base)
            found = parsed["found"]
            entries = parsed["entries"]
            assert isinstance(found, dict) and isinstance(entries, list)
            rows = [
                (PRESENT, None, found["present"]),
                (VOTERS, None, found["voters"]),
                *(
                    (measure, None, found[field])
                    for field, measure in (("blank", BLANK), ("invalid", INVALID))
                    if field in found
                ),
                *((VOTES, label, (position, value)) for label, position, value in entries),
                *(((VOTES, "others", found["dispersed"]),) if "dispersed" in found else ()),
            ]
            counted = sum(int(value) for measure, _, (_, value) in rows if measure in (BLANK, INVALID, VOTES))
            ballots.append(
                {
                    "year": year,
                    "ballot": number,
                    "day": day,
                    "required": int(found["required"][1]),
                    "present": int(found["present"][1]),
                    "voters": int(found["voters"][1]),
                    "counted": counted,
                }
            )
            for measure, label, (position, value) in rows:
                page = box = None
                if item.days:
                    page, x0, y0, x1, y1, word = words[bisect_right(starts, position) - 1]
                    if word.strip(";,.") != value:
                        raise ValueError(f"{year} ballot {number}: {word!r} is not {value!r}")
                    box = [x0, y0, x1, y1]
                values.append(
                    {
                        "year": year,
                        "ballot": number,
                        "measure": measure,
                        "label": label,
                        "value": int(value),
                        "page": page,
                        "box": box,
                    }
                )
            if number == blocks[-1][0]:
                top = max(
                    (
                        (int(value), str(label))
                        for measure, label, (_, value) in rows
                        if measure == VOTES and label != "others"
                    ),
                )
            print(year, number, day, int(found["voters"][1]), counted, entries[:2])
        if not any(word.lower() in elected_text.lower() for word in top[1].split() if len(word) > 3):
            raise ValueError(f"{year}: elected {elected_text!r} is not the last ballot's top {top}")
        elected.append({"year": year, "label": top[1], "text": elected_text.strip()})
        if item.days:
            for number in sorted({int(str(row["page"])) for row in values if row["year"] == year and row["page"]}):
                image = PAGES / f"{artifact.sha256}-{number}.png"
                if not image.exists():
                    subprocess.run(
                        [
                            "pdftoppm",
                            "-r",
                            "100",
                            "-png",
                            "-f",
                            str(number),
                            "-l",
                            str(number),
                            "-singlefile",
                            str(artifact.path),
                            str(image.with_suffix("")),
                        ],
                        check=True,
                    )
                width, height = struct.unpack(">II", image.read_bytes()[16:24])
                pages.append(
                    {
                        "year": year,
                        "page": number,
                        "image": f"pages/{SOURCE}/{image.name}",
                        "width": width,
                        "height": height,
                    }
                )
    pl.DataFrame(ballots).write_parquet(PARSED / "ballots.parquet")
    pl.DataFrame(values, schema_overrides={"box": pl.List(pl.Float32), "page": pl.Int32}).write_parquet(
        PARSED / "values.parquet"
    )
    pl.DataFrame(pages).write_parquet(PARSED / "pages.parquet")
    pl.DataFrame(elected).write_parquet(PARSED / "elected.parquet")


ELECTION_NOTE = (
    "Votes went to names that electors wrote on the ballot, and the record gives most of them by surname alone. Only "
    "the winner is linked to a person."
)
MISMATCH = (
    "The printed rows add up to {counted} ballot papers against {voters} voters; the figures are stored as printed."
)


def emit() -> Path:
    ballots = pl.read_parquet(PARSED / "ballots.parquet")
    values = pl.read_parquet(PARSED / "values.parquet")
    pages = pl.read_parquet(PARSED / "pages.parquet")
    elected = pl.read_parquet(PARSED / "elected.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Italian presidential elections 1948-2015, ballot by ballot in Parliament's joint session",
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        italy = cs.polity("IT")
        unit = cs.unit("it.assembly", "seduta-comune")
        assembly = cs.key("it.body", "presidential-electors")
        president = cs.key("it.office", "president")
        system = cs.key("it.electoral_system", "presidential-election")
        if {unit, assembly, president, system} & cs.minted:
            raise ValueError("publish it.camera before it.presidente")

        result_sets: dict[tuple[int, int], str] = {}
        options: dict[str, str] = {}
        artifacts: dict[int, UUID] = {}
        for item in ELECTIONS:
            year = item.year
            rows = ballots.filter(pl.col("year") == year).sort("ballot")
            days = rows["day"]
            publisher = "Camera dei deputati"
            title = f"Elezione del Presidente della Repubblica {year}, scrutini"
            source = cs.source(publisher, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=publisher,
                title=title,
                url=item.url,
                license=LICENSE,
                notes=(
                    "Each count is as the Camera dei deputati published it for the joint session's ballots, "
                    + (
                        "from the stenographic record, with a page locator into it."
                        if item.days
                        else "from its page on the presidents of the Republic."
                    )
                ),
            )
            artifact = artifacts[year] = cs.artifact(raw.cached(SOURCE, item.url), source)
            for number, image, width, height in (
                pages.filter(pl.col("year") == year).select("page", "image", "width", "height").iter_rows()
            ):
                cs.add(
                    "source_page",
                    id=cs.page(artifact, number),
                    artifact_id=artifact,
                    page_number=number,
                    image_key=image,
                    width=width,
                    height=height,
                )
            winner = cs.key(*item.person)
            if winner in cs.minted:
                raise ValueError(f"{item.person} is not in the archive yet")
            label = elected.filter(pl.col("year") == year)["label"][0]

            election = cs.key("it.election", f"{days[0]}:president")
            cs.add(
                "election",
                id=election,
                polity_id=italy,
                kind="indirect",
                date_from=days[0],
                date_to=days[-1],
                status="held",
            )
            name(election, f"{year} Italian presidential election")
            name(election, f"Elezione del Presidente della Repubblica del {year}", "it", "native")
            path = f"it/{days[0]}-president"
            cs.add("slug", path=path, entity_id=election)
            cs.add("note", entity_id=election, kind="caveat", body_md=ELECTION_NOTE)

            previous: UUID | None = None
            for number, day, required, present, voters, counted in rows.select(
                "ballot", "day", "required", "present", "voters", "counted"
            ).iter_rows():
                contest_key = f"{days[0]}:president:{number}"
                contest = cs.key("it.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=president,
                    body_id=assembly,
                    magnitude=1,
                    round_number=number,
                    previous_round_id=previous,
                    electoral_system_id=system,
                    rules_override={"required": required},
                    default_result_kind="counted",
                )
                name(contest, f"Ballot {number}, {day:%B} {day.day}")
                cs.add("slug", path=f"{path}/ballot-{number}", entity_id=contest)
                previous = contest
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="official",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                if result_set in cs.minted:
                    result_sets[(year, number)] = str(result_set)
                if counted != voters:
                    cs.add(
                        "note",
                        entity_id=result_set,
                        kind="context",
                        body_md=MISMATCH.format(counted=counted, voters=voters),
                    )
                for name_label in values.filter(
                    pl.col("year") == year, pl.col("ballot") == number, pl.col("label").is_not_null()
                )["label"]:
                    option = cs.key("it.option", f"{contest_key}:{slug(name_label)}")
                    options[f"{year}:{number}:{name_label}"] = str(option)
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others" if name_label == "others" else "write_in",
                        ballot_label="Voti dispersi" if name_label == "others" else name_label,
                    )
                    if name_label == label:
                        cs.add(
                            "candidacy",
                            id=cs.key("it.candidacy", f"{contest_key}:{slug(name_label)}"),
                            person_id=winner,
                            option_id=option,
                        )
                        if number == rows["ballot"].max():
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")

        located = values.filter(
            pl.struct("year", "ballot").map_elements(
                lambda r: (r["year"], r["ballot"]) in result_sets, return_dtype=pl.Boolean
            )
        )
        keys = {
            "result_set_id": pl.struct("year", "ballot").map_elements(
                lambda r: result_sets[(r["year"], r["ballot"])], return_dtype=pl.String
            ),
            "unit_id": pl.lit(str(unit)),
            "option_id": pl.concat_str(
                pl.col("year").cast(pl.String), pl.lit(":"), pl.col("ballot").cast(pl.String), pl.lit(":"), "label"
            ).replace_strict(options, default=None),
            "channel": pl.lit(0, pl.Int16),
            "measure": pl.col("measure").cast(pl.Int16),
            "count_round": pl.lit(0, pl.Int16),
        }
        cs.frame(
            "result_value",
            located.select(
                **keys,
                flags=pl.when(pl.col("page").is_not_null()).then(pl.lit(DIGITIZED)).otherwise(pl.lit(0)).cast(pl.Int16),
                value="value",
            ),
        )
        digitized = located.filter(pl.col("page").is_not_null())
        cs.frame(
            "value_locator",
            digitized.select(
                **{key: value for key, value in keys.items()},
                page_id=pl.struct("year", "page").map_elements(
                    lambda r: str(cs.page(artifacts[r["year"]], r["page"])), return_dtype=pl.String
                ),
                x0=pl.col("box").list.get(0),
                y0=pl.col("box").list.get(1),
                x1=pl.col("box").list.get(2),
                y1=pl.col("box").list.get(3),
            ),
        )
        return cs.write()
