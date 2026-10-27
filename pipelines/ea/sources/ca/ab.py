import json
from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import raw
from ea.changeset import NAMESPACE, Changeset
from ea.store import DATA, connect

SOURCE = "ca.ab"
VERSION = "1"
DAY = date(2026, 10, 19)
PAGE = "https://www.elections.ab.ca/elections/referendum/"
ORDERS = {
    109: "https://kings-printer.alberta.ca/Documents/Orders/Orders_in_Council/2026/2026_109.pdf",
    110: "https://kings-printer.alberta.ca/Documents/Orders/Orders_in_Council/2026/2026_110.pdf",
    160: "https://kings-printer.alberta.ca/Documents/Orders/Orders_in_Council/2026/2026_160.pdf",
}
PARSED = DATA / "parsed" / SOURCE
NOTE = (
    "Elections Alberta publishes official results on 29 October 2026. Until then, only the unofficial count on "
    "election night is available, so no results are recorded here yet."
)


def order(number: int) -> int:
    return 110 if number <= 5 else 109 if number <= 9 else 160


def fetch() -> None:
    raw.fetch(SOURCE, PAGE, suffix=".html")
    for url in ORDERS.values():
        raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "questions.json").write_text((DATA / "tmp" / "nh-ab" / "questions.json").read_text())


def emit() -> Path:
    questions = json.loads((PARSED / "questions.json").read_text())["questions"]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2026 Alberta referendum: ten questions with their official wording, results pending",
            polity="CA-AB",
        )

        def name(entity: object, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        alberta = cs.polity("CA-AB")
        if alberta in cs.minted:
            raise ValueError("run ca.elections first: Alberta must exist")
        elections = cs.source("Elections Alberta", "2026 Alberta Referendum")
        cs.add(
            "source",
            id=elections,
            kind="official_body",
            publisher="Elections Alberta",
            title="2026 Alberta Referendum",
            url=PAGE,
        )
        cs.artifact(raw.cached(SOURCE, PAGE), elections)
        orders = {}
        for number, url in ORDERS.items():
            orders[number] = cs.source("Alberta King's Printer", f"Order in Council {number}/2026")
            cs.add(
                "source",
                id=orders[number],
                kind="official_body",
                publisher="Alberta King's Printer",
                title=f"Order in Council {number}/2026",
                url=url,
            )
            cs.artifact(raw.cached(SOURCE, url), orders[number])
        system = cs.key(f"{SOURCE}.electoral_system", "referendum")
        cs.add("electoral_system", id=system, polity_id=alberta, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Alberta referendum")
        election = cs.key(f"{SOURCE}.election", str(DAY))
        cs.add(
            "election", id=election, polity_id=alberta, kind="referendum", date_from=DAY, date_to=DAY, status="held"
        )
        name(election, "2026 Alberta referendum")
        cs.add("slug", path=f"ca/ab/{DAY}-referendum", entity_id=election)
        cs.add("note", entity_id=election, kind="context", body_md=NOTE)
        for question in questions:
            number = question["number"]
            contest = cs.key(f"{SOURCE}.contest", f"{DAY}:{number}")
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type="optional_referendum")
            name(contest, f"Question {number}")
            cs.add("slug", path=f"ca/ab/{DAY}-referendum/question-{number}", entity_id=contest)
            for position, label in enumerate(question["options"], 1):
                cs.add(
                    "contest_option",
                    id=cs.key(f"{SOURCE}.option", f"{DAY}:{number}:{position}"),
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position,
                    ballot_label=label,
                )
            for kind, body, url, source in (
                ("text", question["text"], PAGE, elections),
                ("link", None, ORDERS[order(number)], orders[order(number)]),
            ):
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"{SOURCE}\x1fballot_question_text\x1f{contest}\x1f{kind}"),
                    contest_id=contest,
                    kind=kind,
                    lang="en",
                    body=body,
                    url=url,
                    source_id=source,
                )
        return cs.write()
