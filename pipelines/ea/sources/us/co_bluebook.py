import re
import subprocess
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.co_bluebook"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Colorado General Assembly, Legislative Council Staff"
TITLE = "Ballot Information Booklet (Blue Book)"
BASE = "https://content.leg.colorado.gov/sites/default/files/"
BOOKS = {
    2012: "2012_english_blue_book_internet_version.pdf",
    2013: "2013_blue_book_english_internet.pdf",
    2014: "2014blue_book_for_internet.pdf",
    2015: "blue_book_for_internet_2015_0.pdf",
    2016: "images/2016_english_bluebook_for_the_internet.pdf",
    2018: "images/2018_english_bluebook_for_the_internet.pdf",
    2019: "images/lcs/2019_blue_book_english_for_web.pdf",
    2020: "blue_book_english_for_web_2020_1.pdf",
    2021: "images/2021_blue_book_english.pdf",
    2022: "images/blue_book_2022_english_for_web.pdf",
    2023: "images/blue_book_2023_-_english.pdf",
    2024: "2024-blue-book-english-accessible.pdf",
    2025: "bluebook_2025_english_only_ol-accessible.pdf",
}
STOP = {
    "the",
    "of",
    "and",
    "to",
    "a",
    "an",
    "for",
    "in",
    "on",
    "by",
    "or",
    "with",
    "colorado",
    "state",
    "shall",
    "there",
    "be",
    "change",
    "revised",
    "statutes",
    "amendment",
    "constitution",
    "concerning",
}
TITLE_START = re.compile(r"Shall (?:there be|state|the|a |an |any |[a-z]+ )[^?]{20,1800}\?")


def fetch() -> None:
    for name in BOOKS.values():
        raw.fetch(SOURCE, BASE + name, suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for year, name in BOOKS.items():
        pages = subprocess.run(
            ["pdftotext", str(raw.cached(SOURCE, BASE + name).path), "-"], capture_output=True, text=True, check=True
        ).stdout.split("\f")
        seen: set[str] = set()
        for number, page in enumerate(pages, 1):
            text = re.sub(
                r"\s+", " ", page.replace("“", '"').replace("”", '"').replace("’", "'").replace("\u2011", "-")
            )
            for found in TITLE_START.finditer(text):
                sentence = found[0]
                if sentence not in seen and "ballot title" not in sentence[:80].lower():
                    seen.add(sentence)
                    labels = sorted(
                        {
                            f"{a} {b}"
                            for a, b in re.findall(r"\b(Amendment|Proposition|Referendum)\s+([A-Z]{1,2}|\d+)\b", text)
                        }
                    )
                    rows.append({"year": year, "page": number, "title": sentence, "labels": labels})
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "titles.parquet")
    print(frame.group_by("year").len().sort("year"))


def words(text: str) -> set[str]:
    return {word for word in re.findall(r"[a-z0-9]+", text.lower()) if word not in STOP and len(word) > 2}


def emit() -> Path:
    titles = pl.read_parquet(PARSED / "titles.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Colorado's ballot titles and links to the Blue Book pages from the Legislative Council's ballot information booklets",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://leg.colorado.gov/bluebook",
            license="Public record",
        )
        for name in BOOKS.values():
            cs.artifact(raw.cached(SOURCE, BASE + name), source)
        contests: dict[int, list[tuple[str, str, object]]] = {}
        for key, name, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:CO:question:%%'"""
        ):
            contests.setdefault(int(key[:4]), []).append((key, name, entity))
        placed = 0
        for year, name in BOOKS.items():
            candidates = titles.filter(pl.col("year") == year).to_dicts()
            scored: list[tuple[float, str, int]] = []
            for key, label, _ in contests.get(year, []):
                short = label.split(":", 1)[-1]
                mine = words(short)
                for index, candidate in enumerate(candidates):
                    theirs = words(candidate["title"])
                    if mine:
                        named = re.match(r"(Amendment|Proposition|Referendum) ([A-Z]{1,2}|\d+)", label)
                        boost = 0.3 if named is not None and named[0] in candidate["labels"] else 0
                        scored.append((len(mine & theirs) / len(mine) + boost, key, index))
            taken_keys: set[str] = set()
            taken_titles: set[int] = set()
            for score, key, index in sorted(scored, reverse=True):
                if score < 0.45 or key in taken_keys or index in taken_titles:
                    continue
                taken_keys.add(key)
                taken_titles.add(index)
                entity = next(entity for k, _, entity in contests[year] if k == key)
                label = next(label for k, label, _ in contests[year] if k == key)
                contest = cs.survivors.get(entity, entity)
                candidate = candidates[index]
                address = f"{BASE}{name}#page={candidate['page']}"
                print(f"{year} {label[:50]:50} <- {candidate['title'][:70]} ({score:.2f})")
                for kind, body in (("text", candidate["title"]), ("link", None)):
                    cs.add(
                        "ballot_question_text",
                        id=uuid5(
                            NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{kind}\x1fen\x1f{source}\x1f{address}"
                        ),
                        contest_id=contest,
                        kind=kind,
                        lang="en",
                        body=body,
                        url=address,
                        source_id=source,
                        locator={"page": candidate["page"]},
                    )
                placed += 1
            for key, label, _ in contests.get(year, []):
                if key not in taken_keys:
                    print(f"{year} UNMATCHED {label[:70]}")
        print(f"{placed} contests placed")
        return cs.write()
