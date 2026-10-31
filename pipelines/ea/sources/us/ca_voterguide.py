import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "us.ca_voterguide"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "California Secretary of State"
TITLE = "Official Voter Information Guide"
CDN = "https://vig.cdn.sos.ca.gov/"
GUIDES = {
    date(2014, 6, 3): "2014/primary/pdf",
    date(2014, 11, 4): "2014/general/pdf",
    date(2016, 6, 7): "2016/primary/en/pdf",
    date(2016, 11, 8): "2016/general/en/pdf",
    date(2018, 6, 5): "2018/primary/pdf",
    date(2018, 11, 6): "2018/general/pdf",
    date(2020, 3, 3): "2020/primary/pdf",
    date(2020, 11, 3): "2020/general/pdf",
    date(2022, 11, 8): "2022/general/pdf",
    date(2024, 3, 5): "2024/primary/pdf",
    date(2024, 11, 5): "2024/general/pdf",
    date(2025, 11, 4): "2025/special/pdf",
    date(2012, 11, 6): "2012/general/pdf",
}
NUMBER = re.compile(r"(?i)\bproposition\s+(\d+)\b")


def url(day: date) -> str:
    return f"{CDN}{GUIDES[day]}/complete-vig.pdf"


def fetch() -> None:
    for day in GUIDES:
        raw.fetch(SOURCE, url(day), suffix=".pdf")


def parse() -> None:
    rows: list[dict[str, object]] = []
    for day in GUIDES:
        path = raw.cached(SOURCE, url(day)).path
        if day == date(2014, 11, 4):
            layout = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
            )
            for number, page in enumerate(layout.stdout.split("\f"), 1):
                head = re.search(
                    r"(?m)^\s*Official Title and Summary\s+Prepared by the Attorney General\s*\n\s*\n\s*(.+)\n", page
                )
                numbered = re.search(r"(?m)^\s*(\d{1,2})(?:\s{2,}\S.*)?$", page[:500])
                if head is None or numbered is None:
                    continue
                body = page[head.end() :]
                body = re.split(r"(?i)\n\s*Summary of Legislative Analyst", body)[0]
                body = re.sub(r"(?m)^\s*\d{1,2}(?:\s+|$)", "", body)
                rows.append(
                    {
                        "day": day,
                        "proposition": int(numbered[1]),
                        "title": head[1].strip(),
                        "summary": re.sub(r"\s+", " ", body.replace("•", " ")).strip(),
                        "page": number,
                    }
                )
            continue
        pages = subprocess.run(
            ["pdftotext", "-bbox-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout.split("<page ")[1:]
        for number, page in enumerate(pages, 1):
            blocks: list[tuple[float, float, str]] = []
            for x, y, body in re.findall(r'<block xMin="([\d.]+)" yMin="([\d.]+)"[^>]*>(.*?)</block>', page, re.DOTALL):
                words = [
                    word
                    for left, word in re.findall(r'<word xMin="([\d.]+)"[^>]*>(.*?)</word>', body)
                    if not (float(left) < 45 and word.isdigit())
                ]
                if words:
                    blocks.append((float(x), float(y), re.sub(r"\s+", " ", " ".join(words)).replace("&amp;", "&")))
            texts = [text for _, _, text in blocks]
            if "OFFICIAL TITLE AND SUMMARY" not in texts or not any(
                text.startswith("PREPARED BY THE ATTORNEY GENERAL") for text in texts
            ):
                continue
            at = texts.index("OFFICIAL TITLE AND SUMMARY")
            numbers = [text for x, y, text in blocks[:at] if text.isdigit() and y < 60 and x > 45]
            if not numbers:
                numbers = [text for x, y, text in blocks[:at] if text.isdigit() and y < 60]
            title = texts[at - 1]
            if title.isdigit() or title == "PROPOSITION":
                print(f"{day} page {number}: no title ({texts[at - 3 : at]})")
                continue
            stop = re.compile(r"(?i)(summary of legislative|final votes|analysis by)")
            after = blocks[at + 1 :]
            fiscal = next((block for block in after if stop.match(block[2])), None)
            if fiscal is not None and fiscal[0] > 250:
                limit = min(block[1] for block in after if re.match(r"(?i)(final votes|analysis by)", block[2]))
                chosen = sorted(
                    (
                        block
                        for block in after
                        if (block[0] < 250 and block[1] < limit) or (block[0] > 250 and block[1] < fiscal[1])
                    ),
                    key=lambda block: (block[0] > 250, block[1]),
                )
            else:
                chosen = after[: after.index(fiscal)] if fiscal is not None else after
            summary: list[str] = []
            for _, _, text in chosen:
                if text.startswith("PREPARED BY THE ATTORNEY GENERAL") or stop.match(text):
                    continue
                if text.startswith("The text of this measure") or text.isdigit() or text in title:
                    continue
                summary.append(text)
            bullets = [re.sub(r"\s+", " ", " ".join(summary).replace("•", " ")).strip(" .")]
            rows.append(
                {
                    "day": day,
                    "proposition": int(numbers[-1]) if numbers else None,
                    "title": title,
                    "summary": (bullets[0] + ".") if bullets[0] else "",
                    "page": number,
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame = pl.DataFrame(rows)
    frame.write_parquet(PARSED / "propositions.parquet")
    print(frame.group_by("day").len().sort("day"))


def emit() -> Path:
    guide = pl.read_parquet(PARSED / "propositions.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="California's ballot titles, summaries, and links to the guide pages from the Official Voter Information Guides",
            polity="US",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://voterguide.sos.ca.gov/",
            license="Public record",
        )
        for day in GUIDES:
            cs.artifact(raw.cached(SOURCE, url(day)), source)
        contests: dict[tuple[date, int], list[str]] = {}
        for key, text, entity in conn.execute(
            """select x.value, n.text, x.entity_id from external_id x join ballot_question q on q.contest_id = x.entity_id
               join name n on n.entity_id = x.entity_id and n.is_preferred
               where x.scheme = 'us.contest' and x.value like '%%:CA:question:%%'"""
        ):
            if (found := NUMBER.search(text)) is not None:
                contests.setdefault((date.fromisoformat(key[:10]), int(found[1])), []).append(entity)
        placed = 0
        for day, proposition, title, summary, page in guide.iter_rows():
            entities = contests.get((day, proposition), [])
            if len(entities) != 1:
                print(f"{day} Proposition {proposition}: {len(entities)} contests ({title[:50]})")
                continue
            contest = cs.survivors.get(entities[0], entities[0])
            link = f"{url(day)}#page={page}"
            for kind, body, address in (("text", title, link), ("summary", summary, link), ("link", None, link)):
                if body == "":
                    continue
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{kind}\x1fen\x1f{source}\x1f{address}"),
                    contest_id=contest,
                    kind=kind,
                    lang="en",
                    body=body,
                    url=address,
                    source_id=source,
                    locator={"page": page},
                )
            placed += 1
        print(f"{placed} of {guide.height} propositions placed")
        return cs.write()
