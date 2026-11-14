import html
import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "gb.parish_polls"
VERSION = "1"
ROWS = Path(__file__).with_name("parish_polls.tsv")
VOTES, REGISTERED, BALLOTS, INVALID = 1, 2, 3, 5
CONTEXT = {
    "E": (
        "A parish poll under paragraph 18 of Schedule 12 to the Local Government Act 1972, demanded at a parish "
        "meeting and run by {council} under the Parish and Community Meetings (Polls) Rules 1987. The result is "
        "advisory."
    ),
    "W": (
        "A community poll under paragraph 34 of Schedule 12 to the Local Government Act 1972, demanded at a "
        "community meeting and run by {council}. The result is advisory."
    ),
}


def suffix(url: str) -> str:
    return ".pdf" if url.split("?")[0].lower().endswith(".pdf") or "/document" in url.lower() else ".html"


def fetch() -> None:
    for url in sorted(set(pl.read_csv(ROWS, separator="\t", infer_schema=False)["url"])):
        raw.fetch(SOURCE, url, suffix=suffix(url))


def printed(url: str) -> set[str]:
    path = raw.cached(SOURCE, url).path
    if path.read_bytes()[:4] == b"%PDF":
        text = subprocess.run(
            ["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True
        ).stdout
    else:
        text = html.unescape(re.sub(r"(?s)<(script|style).*?</\1>|<[^>]+>", " ", path.read_text()))
    return set(re.findall(r"\d+(?:\.\d+)?", re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)))


def emit() -> Path:
    table = pl.read_csv(ROWS, separator="\t", infer_schema=False)
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Parish and community polls in England and Wales, from the principal councils' notices of result",
            polity="GB",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def named(entity: UUID) -> tuple[str, str]:
            found = conn.execute(
                "select n.text, s.path from name n join slug s on s.entity_id = n.entity_id"
                " where n.entity_id = %s and n.is_preferred and n.lang = 'en' order by s.is_current desc limit 1",
                [entity],
            ).fetchone()
            if found is None:
                raise ValueError(f"{entity}: no name or slug")
            return found[0], found[1]

        system = cs.key("gb.electoral_system", "referendum")
        if system in cs.minted:
            raise ValueError("publish gb.referendums first")
        sources: dict[str, UUID] = {}
        units: dict[str, UUID] = {}
        for (day, council, parish), questions in table.group_by(["day", "council", "parish"], maintain_order=True):
            council_polity = cs.key("gb.polity", str(council))
            polity = cs.key("gb.polity", str(parish))
            if council_polity in cs.minted or polity in cs.minted:
                raise ValueError(f"{parish}: the parish and its council must exist")
            council_name, _ = named(council_polity)
            parish_name, path = named(polity)
            welsh = str(parish).startswith("W")
            noun = "community poll" if welsh else "parish poll"
            held = date.fromisoformat(str(day))
            election = cs.key("gb.election", f"{day}:parish_poll:{parish}")
            cs.add(
                "election",
                id=election,
                polity_id=polity,
                parent_id=None,
                kind="referendum",
                date_from=held,
                date_to=held,
                status="held",
            )
            name(election, f"{parish_name} {noun}, {held:%-d %B %Y}")
            prefix = f"{path}/{day}-{noun.replace(' ', '-')}"
            cs.add("slug", path=prefix, entity_id=election)
            unit = units.get(str(parish))
            if unit is None:
                unit = units[str(parish)] = cs.unit("gb.parish", str(parish))
                if unit in cs.minted:
                    cs.add(
                        "reporting_unit",
                        id=unit,
                        polity_id=polity,
                        scheme_code="gb.parish",
                        code=str(parish),
                        area_relation="equals",
                        area_id=cs.area("gb.parish", str(parish)),
                        kind="standard",
                        valid_from=None,
                    )
                    name(unit, parish_name)
            for row in questions.iter_rows(named=True):
                url = row["url"]
                numbers = printed(url)
                for field in ("yes", "no", "rejected", "electorate", "ballots", "turnout"):
                    if row[field] is not None and row[field] not in numbers:
                        raise ValueError(f"{parish_name} {day}: {field} {row[field]} is not printed in {url}")
                title = row["title"]
                source = sources.get(url)
                if source is None:
                    publisher = parish_name if ".parish." in url else council_name
                    source = sources[url] = cs.source(publisher, title)
                    if source in cs.minted:
                        cs.add("source", id=source, kind="official_body", publisher=publisher, title=title, url=url)
                    cs.artifact(raw.cached(SOURCE, url), source)
                key = f"{day}:parish_poll:{parish}:{row['question_no']}"
                contest = cs.key("gb.contest", key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    district_area_id=cs.area("gb.parish", str(parish)),
                    electoral_system_id=system,
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add("ballot_question", contest_id=contest, type="optional_referendum")
                name(contest, row["label"])
                cs.add("slug", path=f"{prefix}/question-{row['question_no']}", entity_id=contest)
                cs.add(
                    "note",
                    entity_id=contest,
                    kind="context",
                    body_md=CONTEXT["W" if welsh else "E"].format(council=council_name),
                )
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE_URL, f"{SOURCE}\x1fballot_question_text\x1f{contest}"),
                    contest_id=contest,
                    kind="text",
                    lang="en",
                    body=row["question"],
                    url=url,
                    source_id=source,
                )
                options = [cs.key(f"{SOURCE}.option", f"{key}:{position}") for position in range(2)]
                for position, (option, answer) in enumerate(zip(options, ("Yes", "No"), strict=True)):
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position + 1,
                        ballot_label=answer,
                    )
                yes, no = int(row["yes"]), int(row["no"])
                result_set = cs.key(f"{SOURCE}.result_set", key)
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
                cs.add("citation", entity_id=result_set, source_id=source)
                if row["caveat"] is not None:
                    cs.add("note", entity_id=result_set, kind="caveat", body_md=row["caveat"])
                if yes != no:
                    cs.add("outcome", result_set_id=result_set, option_id=options[no > yes], kind="won")
                for field, option, measure in (
                    ("yes", options[0], VOTES),
                    ("no", options[1], VOTES),
                    ("rejected", None, INVALID),
                    ("electorate", None, REGISTERED),
                    ("ballots", None, BALLOTS),
                ):
                    value = row[field]
                    if value is None:
                        continue
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=int(value),
                    )
        return cs.write()
