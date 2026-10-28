import difflib
import html
import json
import re
import subprocess
import tempfile
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.measures_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date(2026, 11, 3)
MEASURES = Path(__file__).with_suffix(".json")
BROWSER = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}


def measures() -> list[dict[str, str | int | None]]:
    return json.loads(MEASURES.read_text())


def plain(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", html.unescape(text).lower())


def fetch() -> None:
    for url in sorted({str(row["url"]) for row in measures()}):
        try:
            raw.cached(SOURCE, url)
            continue
        except LookupError:
            pass
        response = httpx.get(url, headers=BROWSER, follow_redirects=True, timeout=300, verify=False)
        if response.status_code != 200:
            print("not fetched:", response.status_code, url)
            continue
        raw.store(
            SOURCE,
            url,
            response.content,
            suffix=".pdf" if response.content.startswith(b"%PDF") else ".html",
            retrieved_at=datetime.now(UTC),
            media_type=response.headers.get("content-type", ""),
        )


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    documents: dict[str, list[str]] = {}
    scanned: set[str] = set()
    rows = []
    for row in measures():
        url = str(row["url"])
        try:
            path = raw.cached(SOURCE, url).path
        except LookupError:
            print("no source document:", row["state"], row["label"], url)
            continue
        if url not in documents:
            extracted = (
                [
                    subprocess.run(
                        ["pdftotext", *mode, str(path), "-"], capture_output=True, text=True, check=True
                    ).stdout
                    for mode in ([], ["-raw"], ["-layout"])
                ]
                if path.suffix == ".pdf"
                else [re.sub(r"<[^>]+>", " ", path.read_text(errors="replace"))]
            )
            if path.suffix == ".pdf" and len(plain(extracted[0])) < 200:
                with tempfile.TemporaryDirectory() as scratch:
                    subprocess.run(["pdftoppm", "-r", "300", "-gray", "-png", str(path), f"{scratch}/p"], check=True)
                    extracted = [
                        " ".join(
                            subprocess.run(
                                ["tesseract", str(page), "-"], capture_output=True, text=True, check=True
                            ).stdout
                            for page in sorted(Path(scratch).glob("p-*.png"))
                        )
                    ]
                scanned.add(url)
            documents[url] = [plain(text) for text in extracted] + [
                plain(re.sub(r"(?m)^\s*\d{1,2}(\s+|$)", "", text)) for text in extracted
            ]
        segments = [
            plain(part) for part in re.split(r"(?<=[.?!:;—])\s+|\n", str(row["text"])) if len(plain(part)) > 3
        ]
        found = row["text"] is not None and any(
            all(segment in document for segment in segments)
            or all(re.sub("f[il]", "", segment) in re.sub("f[il]", "", document) for segment in segments)
            for document in documents[url]
        )
        if not found and row["text"] is not None and url in scanned:
            document = documents[url][0]
            found = all(
                difflib.SequenceMatcher(
                    None, segment, document[max(match.b - match.a, 0) : max(match.b - match.a, 0) + len(segment)]
                ).ratio()
                >= 0.95
                for segment in segments
                for match in [
                    difflib.SequenceMatcher(None, segment, document, autojunk=False).find_longest_match(
                        0, len(segment), 0, len(document)
                    )
                ]
            )
        if not found:
            print("text not found in source:", row["state"], row["label"], url)
        rows.append({**{key: None if value is None else str(value) for key, value in row.items()}, "verified": found})
    pl.DataFrame(rows, infer_schema_length=None).write_parquet(PARSED / "measures.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot measures on the November 3, 2026 ballot, with official wording",
            polity="US",
        )
        system = cs.key("us.electoral_system", "referendum")
        states = dict(
            conn.execute(
                """select substr(p.iso_code, 4), u.area_id from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        )
        paths = dict(
            conn.execute(
                """select x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
                   where x.scheme = 'us.election' and x.value like %s""",
                [f"{DAY}:%"],
            ).fetchall()
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        held = dict(
            conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        labels = Counter(zip(frame["state"], frame["label"]))
        sources = {}
        for row in frame.iter_rows(named=True):
            state = row["state"]
            if (row["publisher"], row["source_title"]) not in sources:
                source = cs.source(row["publisher"], row["source_title"])
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=row["publisher"],
                    title=row["source_title"],
                    url=row["url"],
                )
                sources[row["publisher"], row["source_title"]] = source
            source = sources[row["publisher"], row["source_title"]]
            cs.artifact(raw.cached(SOURCE, row["url"]), source)
            election_key = f"{DAY}:{state}"
            election = cs.key("us.election", election_key)
            designation = row["label"] if labels[state, row["label"]] == 1 else f"{row['label']} {row['title']}"
            contest_key = f"{DAY}:{state}:question:{slug(designation)}"
            contest = cs.key("us.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=states[state],
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type=row["type"])
            name = f"{row['label']}: {row['name'] or row['title'].removesuffix('.')}" if row["title"] else row["label"]
            cs.add("name", entity_id=contest, text=name, lang="en", kind="official", is_preferred=True)
            if contest in cs.minted:
                path = held.get(contest)
                if path is None:
                    base = f"{paths[election_key]}/{slug(designation)[:60]}"
                    path, n = base, 1
                    while path in taken:
                        n += 1
                        path = f"{base}-{n}"
                    taken.add(path)
                cs.add("slug", path=path, entity_id=contest)
            for position, answer in enumerate((row.get("yes") or "Yes", row.get("no") or "No")):
                cs.add(
                    "contest_option",
                    id=cs.key("us.option", f"{contest_key}:{('yes', 'no')[position]}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=answer,
                )
            cs.add(
                "citation",
                entity_id=contest,
                source_id=source,
                locator={"page": int(row["page"])} if row["page"] else None,
            )
            if row["verified"]:
                cs.add(
                    "ballot_question_text",
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=row["text"],
                    url=row["url"],
                    source_id=source,
                    locator={"page": int(row["page"])} if row["page"] else None,
                )
        print(frame.group_by("state").agg(pl.len(), pl.col("verified").sum()).sort("state"))
        return cs.write()
