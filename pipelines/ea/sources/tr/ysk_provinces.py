import re
import subprocess
from datetime import date
from pathlib import Path

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.tr.ysk import (
    CANDIDATES,
    CANDIDATES_2014,
    CANDIDATES_2018,
    INVALID,
    LICENSE,
    PROVINCES,
    PUBLISHER,
    REGISTERED,
    RUNOFF,
    VALID,
    VOTERS,
    VOTES,
)
from ea.store import connect

SOURCE = "tr.ysk_provinces"
VERSION = "2"
DOCS = "https://www.ysk.gov.tr/doc/dosyalar/docs"
BOOK = f"{DOCS}/2009_2019_%C4%B0STAT%C4%B0ST%C4%B0K_K%C4%B0TABI.pdf"
PROVINCES_2014 = f"{DOCS}/2014CB/CB-AdayOylari-ilBaz%C4%B1nda-2014.pdf"
BOOKLET_2023 = (
    f"{DOCS}/14%20May%C4%B1s%202023%20Cumhurba%C5%9Fkan%C4%B1%20ve%2025.%20D%C3%B6nem%20MV%20Genel"
    "%20Se%C3%A7imleri%20%C4%B0statistikleri.pdf"
)
BULLETIN_2023 = f"{DOCS}/28Mayis2023CBSecimiIkinciOylamaBulteni.pdf"
TITLES = {
    BOOK: "Seçim İstatistikleri 2009–2019 (Cumhurbaşkanı Seçimi, İllere Göre Aldıkları Oy Sayıları)",
    PROVINCES_2014: "Onikinci Cumhurbaşkanı Seçimi: Adayların İllerde Aldıkları Oy Sayıları (Cezaevi Dahil)",
    BOOKLET_2023: "14 Mayıs 2023 Cumhurbaşkanı ve 28. Dönem Milletvekili Genel Seçimleri İstatistikleri",
    BULLETIN_2023: "28 Mayıs 2023 Cumhurbaşkanı Seçimi İkinci Oylama Seçim İstatistikleri Bülteni",
}
NOTES = (
    "Official province tables of the Supreme Election Council (YSK) for domestic votes, prisons included. They sum "
    "to the gazette decision's domestic figures, which this set keeps for votes abroad and at customs gates."
)
FOLD = str.maketrans({"i": "İ", "ı": "I"})
NAMES = {province.translate(FOLD).upper(): f"{plate:02}" for plate, province in enumerate(PROVINCES, 1)}
NUMBER = r"\d[\d.]*"
CONTESTS = (
    (date(2014, 8, 10), PROVINCES_2014, CANDIDATES_2014, (0, 1, 2)),
    (date(2018, 6, 24), BOOK, CANDIDATES_2018, (5, 1, 0, 2, 3, 4)),
    (date(2023, 5, 14), BOOKLET_2023, CANDIDATES, (0, 1, 2, 3)),
    (date(2023, 5, 28), BULLETIN_2023, CANDIDATES, RUNOFF),
)


def fetch() -> None:
    for url in TITLES:
        raw.fetch(SOURCE, url, suffix=".pdf")


def emit() -> Path:
    texts = {
        url: subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, url).path), "-"], capture_output=True, text=True, check=True
        ).stdout.splitlines()
        for url in TITLES
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Turkey's presidential elections of 2014, 2018, and 2023 by province, from YSK's statistics",
            polity="TR",
        )
        sources = {}
        for url, title in TITLES.items():
            sources[url] = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=sources[url],
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=url,
                license=LICENSE,
                notes=NOTES,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[url])
        for day, url, candidates, columns in CONTESTS:
            lines = texts[url]
            heading = (
                0
                if day.year == 2014
                else next(
                    index
                    for index, line in enumerate(lines)
                    if re.search(r"\d\.1\.1\.2\.\s+Adayların İllere Göre", line)
                    and "...." not in line
                    and (day.year != 2018 or any("2018" in before for before in lines[max(0, index - 8) : index]))
                )
            )
            votes: dict[str, list[int]] = {}
            for line in lines[heading:] if day.year != 2014 else lines:
                if day.year == 2014:
                    tokens = line.split()
                    if len(tokens) == 16 and tokens[0].isdigit():
                        votes[f"{int(tokens[0]):02}"] = [
                            int(tokens[index].replace(".", "")) for index in (3, 4, 6, 8, 10, 12, 14)
                        ]
                    continue
                found = re.match(rf"^\s*(\D+?)\s+((?:{NUMBER}\s+){{{len(columns) - 1}}}{NUMBER})\s*$", line)
                if found is not None and found[1].strip() in NAMES:
                    votes[NAMES[found[1].strip()]] = [int(value.replace(".", "")) for value in found[2].split()]
                if len(votes) == 81:
                    break
            if sorted(votes) != sorted(NAMES.values()):
                raise ValueError(f"{day}: read {len(votes)} provinces")
            turnout: dict[str, tuple[int, int]] = {}
            if day == date(2023, 5, 14):
                start = next(
                    index
                    for index, line in enumerate(lines)
                    if "5.2." in line and "Cumhurbaşkanı Seçimine" in line and "...." not in line
                )
                stop = next(index for index in range(start, len(lines)) if "5.3." in lines[index])
                for line in lines[start:stop]:
                    for name, registered, voters in re.findall(rf"([^\W\d]+)\s+({NUMBER})\s+({NUMBER})\s+[\d,]+", line):
                        if name in NAMES:
                            turnout[NAMES[name]] = (int(registered.replace(".", "")), int(voters.replace(".", "")))
                if len(turnout) != 81:
                    raise ValueError(f"{day}: read turnout for {len(turnout)} provinces")

            contest_key = f"{day}:president"
            contest = cs.key("tr.contest", contest_key)
            if contest in cs.minted:
                raise ValueError("publish tr.ysk before tr.ysk_provinces")
            old = conn.execute(
                "select id, k, source_id, completeness, election_id from result_set where contest_id = %s and is_default and status = 'active'",
                [contest],
            ).fetchone()
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            if old is not None and old[0] == result_set:
                old = conn.execute(
                    """select p.id, p.k, p.source_id, p.completeness, p.election_id
                    from result_set p join result_set s on s.supersedes_id = p.id where s.id = %s""",
                    [result_set],
                ).fetchone()
            if old is None:
                raise ValueError(f"{day}: no default set")
            option_ids = [cs.key("tr.option", f"{contest_key}:{slug(candidates[index][0])}") for index in columns]

            domestic = {
                option: value
                for option, value in conn.execute(
                    """select co.id, v.value from result_value v
                    join reporting_unit ru on ru.k = v.unit_k and ru.code = 'domestic' and ru.scheme_code = 'tr.nation'
                    join contest_option co on co.k = v.option_k
                    where v.result_set_k = %s and v.measure = %s""",
                    [old[1], VOTES],
                ).fetchall()
            }
            offset = 4 if day.year == 2014 else 0
            for column, option in enumerate(option_ids):
                total = sum(row[offset + column] for row in votes.values())
                if domestic.get(option) != total:
                    raise ValueError(f"{day}: column {column} sums to {total}, gazette domestic {domestic.get(option)}")

            cs.add(
                "result_set",
                id=old[0],
                election_id=old[4],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="superseded",
                source_id=old[2],
                is_default=False,
                completeness=old[3],
            )
            cs.add(
                "result_set",
                id=result_set,
                election_id=old[4],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[url],
                is_default=True,
                completeness=1,
                supersedes_id=old[0],
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTES)
            for option, kind, seats, notes, candidacy in conn.execute(
                "select option_id, kind, seats, notes, candidacy_id from outcome where result_set_id = %s", [old[0]]
            ).fetchall():
                cs.add(
                    "outcome",
                    result_set_id=result_set,
                    option_id=option,
                    kind=kind,
                    seats=seats,
                    notes=notes,
                    candidacy_id=candidacy,
                )
            for plate, row in votes.items():
                unit = cs.unit("tr.il", plate)
                facts = [(option, VOTES, row[offset + column]) for column, option in enumerate(option_ids)]
                if day.year == 2014:
                    facts += [
                        (None, REGISTERED, row[0]),
                        (None, VOTERS, row[1]),
                        (None, VALID, row[2]),
                        (None, INVALID, row[3]),
                    ]
                if plate in turnout:
                    facts += [(None, REGISTERED, turnout[plate][0]), (None, VOTERS, turnout[plate][1])]
                for option, measure, value in facts:
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit,
                        option_id=option,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
            for unit, option, channel, measure, count_round, flags, value in conn.execute(
                """select ru.id, co.id, v.channel, v.measure, v.count_round, v.flags, v.value from result_value v
                join reporting_unit ru on ru.k = v.unit_k and ru.scheme_code = 'tr.nation' and ru.code in ('abroad', 'customs')
                left join contest_option co on co.k = v.option_k and v.option_k <> 0
                where v.result_set_k = %s""",
                [old[1]],
            ).fetchall():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=channel,
                    measure=measure,
                    count_round=count_round,
                    flags=flags,
                    value=value,
                )
        return cs.write()
