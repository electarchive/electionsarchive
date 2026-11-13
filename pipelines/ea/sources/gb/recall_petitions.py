import csv
import html
import re
import subprocess
from datetime import UTC, date, datetime
from pathlib import Path

import httpx

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import connect

SOURCE = "gb.recall_petitions"
VERSION = "1"
VOTES, REGISTERED = 1, 2
ROWS = Path(__file__).with_name("recall_petitions.tsv")


def rows() -> list[dict[str, str]]:
    with ROWS.open(newline="") as handle:
        return list(csv.DictReader(handle, delimiter="\t"))


SNAPSHOT = "https://web.archive.org/web/20260911174639id_/"


def fetch() -> None:
    for url in sorted({row["url"] for row in rows()}):
        if url.endswith(".pdf"):
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                response = httpx.get(SNAPSHOT + url, follow_redirects=True, timeout=600)
                response.raise_for_status()
                raw.store(
                    SOURCE,
                    url,
                    response.content,
                    suffix=".pdf",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/pdf",
                )
        else:
            raw.fetch(SOURCE, url, suffix=".json")


def printed(url: str) -> set[str]:
    path = raw.cached(SOURCE, url).path
    if path.suffix == ".pdf":
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, check=True).stdout.decode()
    else:
        text = html.unescape(
            re.sub(r"(?s)<(script|style).*?</\1>|<[^>]+>", " ", path.read_bytes().decode("utf-8", errors="replace"))
        )
    return set(re.findall(r"\d+", re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)))


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Recall of MPs Act 2015 petitions that reached a count: North Antrim 2018, Peterborough 2019, "
            "Brecon and Radnorshire 2019, Rutherglen and Hamilton West 2023, and Wellingborough 2023",
            polity="GB",
        )
        system = cs.key("gb.electoral_system", "referendum")
        if system in cs.minted:
            raise ValueError("publish gb.referendums first")
        polity = cs.polity("GB")
        base = conn.execute(
            "select path from slug where entity_id = %s order by is_current desc limit 1", [polity]
        ).fetchone()
        assert base is not None
        for row in rows():
            source = cs.source(row["publisher"], row["title"])
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=row["publisher"],
                    title=row["title"],
                    url=row["url"],
                )
                cs.artifact(raw.cached(SOURCE, row["url"]), source)
            found = printed(row["url"])
            eligible, signed = int(row["eligible"]), int(row["signed"])
            for figure in (eligible, signed):
                if str(figure) not in found:
                    raise ValueError(f"{row['key']}: {figure} is not printed in {row['url']}")
            success = signed * 10 >= eligible
            if success != (row["outcome"] == "successful"):
                raise ValueError(f"{row['key']}: signatures disagree with the declared outcome")
            first, last = date.fromisoformat(row["from"]), date.fromisoformat(row["to"])
            area = cs.area("gb.pcon", row["code"])
            election = cs.key("gb.election", f"{last}:recall:{row['key']}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=None,
                kind="recall",
                date_from=first,
                date_to=last,
                status="held",
            )
            cs.add(
                "name",
                entity_id=election,
                text=f"{last.year} {row['constituency']} recall petition",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            election_path = f"{base[0]}/{last}-recall-{slug(row['constituency'])}"
            cs.add("slug", path=election_path, entity_id=election)
            contest = cs.key("gb.contest", f"{last}:recall:{row['key']}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                district_area_id=area,
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="recall_question")
            cs.add(
                "name",
                entity_id=contest,
                text=f"Recall petition for {row['mp']}, Member for {row['constituency']}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=f"{election_path}/petition", entity_id=contest)
            cs.add("note", entity_id=contest, kind="context", body_md=row["context"])
            option = cs.key("gb.recall_petitions.option", row["key"])
            cs.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="answer",
                ballot_position=1,
                ballot_label="Signed the petition",
            )
            result_set = cs.key("gb.recall_petitions.result_set", row["key"])
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
            if success:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind="won")
            unit = cs.unit("gb.pcon", row["code"])
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=polity,
                    scheme_code="gb.pcon",
                    code=row["code"],
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                cs.add("name", entity_id=unit, text=row["constituency"], lang="en", kind="official", is_preferred=True)
            for opt, measure, value in ((option, VOTES, signed), (None, REGISTERED, eligible)):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=opt,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
