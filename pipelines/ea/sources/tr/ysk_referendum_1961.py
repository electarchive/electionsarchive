import subprocess
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import ANSWERS_2017, INVALID, LICENSE, PROVINCES, PUBLISHER, REGISTERED, VALID, VOTERS, VOTES
from ea.store import connect

SOURCE = "tr.ysk_referendum_1961"
VERSION = "1"
DAY = date(1961, 7, 9)
URL = "https://ysk.gov.tr/doc/dosyalar/docs/1961Referandum/9-Temmuz-1961-Halk-Oylamas%C4%B1.pdf"
TITLE = "9 Temmuz 1961 Anayasa Değişikliği Halk Oylaması Sonucu"
NOTES = (
    "YSK's table of the July 9, 1961 referendum by province, 67 rows with boxes, registered voters, participants, "
    "valid and invalid votes, and Evet and Hayır votes. It sums to a few hundred votes away from the totals YSK "
    "decision 106 printed in the Official Gazette, so the two sets disagree slightly and this finer one is the default."
)
FOLD = str.maketrans({"i": "İ", "ı": "I"})
NAMES = {province.translate(FOLD).upper(): f"{plate:02}" for plate, province in enumerate(PROVINCES, 1)}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".pdf")


def emit() -> Path:
    lines = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, URL).path), "-"], capture_output=True, text=True, check=True
    ).stdout.splitlines()
    rows: dict[str, list[int]] = {}
    for line in lines:
        tokens = line.split()
        if len(tokens) == 9 and tokens[0].isdigit() and tokens[1] in NAMES:
            rows[NAMES[tokens[1]]] = [int(token.replace(".", "")) for token in tokens[2:]]
    if len(rows) != 67:
        raise ValueError(f"read {len(rows)} provinces")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Turkey's 1961 constitutional referendum by province, from YSK's table",
            polity="TR",
        )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=URL,
            license=LICENSE,
            notes=NOTES,
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        contest_key = f"{DAY}:constitution"
        contest = cs.key("tr.contest", contest_key)
        result_set = cs.key(f"{SOURCE}.result_set", contest_key)
        old = conn.execute(
            "select id, source_id, completeness, election_id from result_set where contest_id = %s and id <> %s",
            [contest, result_set],
        ).fetchone()
        if old is None:
            raise ValueError("no predecessor set")
        options = [cs.key("tr.option", f"{contest_key}:{slug(answer)}") for answer in ANSWERS_2017]
        cs.add(
            "result_set",
            id=old[0],
            election_id=old[3],
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="superseded",
            source_id=old[1],
            is_default=False,
            completeness=old[2],
        )
        cs.add(
            "result_set",
            id=result_set,
            election_id=old[3],
            contest_id=contest,
            stage="certified",
            kind="counted",
            status="active",
            source_id=source,
            is_default=True,
            completeness=1,
            supersedes_id=old[0],
        )
        cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTES)
        cs.add("outcome", result_set_id=result_set, option_id=options[0], kind="won")
        for plate, (_, registered, voters, valid, invalid, yes, no) in rows.items():
            for option, measure, value in (
                (None, REGISTERED, registered),
                (None, VOTERS, voters),
                (None, VALID, valid),
                (None, INVALID, invalid),
                (options[0], VOTES, yes),
                (options[1], VOTES, no),
            ):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("tr.il", plate),
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        return cs.write()
