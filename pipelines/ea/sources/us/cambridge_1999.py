import json
import re
import subprocess
import unicodedata
from datetime import date
from pathlib import Path
from uuid import UUID

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.cambridge_1999"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Cambridge Election Commission"
ORIGIN = "http://www.ci.cambridge.ma.us:80/~Election/{}"
WAYBACK = "https://web.archive.org/web/{}id_/{}"
DAY = date(1999, 11, 2)
SHEETS = {
    "council": (
        "20000925121520",
        "99council.pdf",
        9,
        "alged:2511000_1999_11_cambridge_MA_City Council_synthetic at-large d_9",
    ),
    "school": ("20000925121529", "99school.pdf", 6, None),
}
WRITE_IN = ("POSITION 1", "OTHER POSITIONS", "WRITTEN-IN")
METHOD = "Data from the Cambridge Election Commission's certified proportional representation count sheet. Count 1 is the tally of first choices. Each later count transfers the ballots of one elected or eliminated candidate; every count adds up to the sheet's total. The quota is calculated by dividing the number of valid ballots by the number of seats plus one, dropping any fraction, and adding one."
DECLARED = (
    "Lists only the elected candidates, without vote counts. The full count from the certified sheet is the default."
)


def fetch() -> None:
    for stamp, path, _, _ in SHEETS.values():
        raw.fetch(SOURCE, WAYBACK.format(stamp, ORIGIN.format(path)), suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    sheets = {}
    for kind, (stamp, path, seats, _) in SHEETS.items():
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, WAYBACK.format(stamp, ORIGIN.format(path))).path), "-"],
            check=True,
            capture_output=True,
            text=True,
        ).stdout
        lines = text.splitlines()
        totals = [number for number, line in enumerate(lines) if line.startswith("TOTAL")]
        rows: dict[str, list[int]] = {}
        starts = [0, *[number + 1 for number in totals[:-1]]]
        for block, (start, end) in enumerate(zip(starts, totals, strict=True)):
            anchors = [match.end() for match in re.finditer(r"-?\d+", lines[end])]
            width = len(anchors)
            for line in lines[start : end + 1]:
                if len(line) - len(line.lstrip()) > 3 or re.search(r"\d(ST|ND|RD|TH)\b", line):
                    continue
                cells = [0] * width
                begin = None
                for match in re.finditer(r"-?\d+", line):
                    column = min(range(width), key=lambda index: abs(anchors[index] - match.end()))
                    if abs(anchors[column] - match.end()) <= 4:
                        cells[column] = int(match[0])
                        begin = match.start() if begin is None else begin
                label = re.sub(r"\s+", " ", line[:begin]).strip() if begin is not None else ""
                if label:
                    rows.setdefault(label, []).extend(cells if block else [0, *cells])
        counts: dict[str, list[int]] = {}
        for label, cells in rows.items():
            pairs = list(zip(cells[0::2], cells[1::2], strict=True))
            counts[label] = [result for _, result in pairs]
            for number in range(1, len(pairs)):
                if pairs[number - 1][1] + pairs[number][0] != pairs[number][1]:
                    raise ValueError(f"{kind}: {label} count {number + 1} does not follow from the transfer")
        total = counts.pop("TOTAL")
        for number, value in enumerate(total):
            if sum(values[number] for values in counts.values()) != value:
                raise ValueError(f"{kind}: count {number + 1} does not add up to the total")
        invalid = counts.pop("INVALID")[0]
        valid = total[0] - invalid
        quota = valid // (seats + 1) + 1
        ((line, column),) = [(number, line.index("Quota")) for number, line in enumerate(lines) if "Quota" in line]
        printed_quota = [
            int(m[0]) for m in re.finditer(r"\d+", lines[line + 1]) if column - 2 <= m.start() <= column + 20
        ]
        if printed_quota != [quota]:
            raise ValueError(f"{kind}: the printed quota is not {quota}")
        elected = [label for label in counts if label not in (*WRITE_IN, "EXHAUSTED") and counts[label][-1] >= quota]
        printed = re.findall(r"\b\d\s+([A-Z][\w-]+)[,.] [A-Z]", text.rsplit("ELECTION", 1)[-1])
        if len(elected) != seats or {label.split(",")[0].split(".")[0] for label in elected} != set(printed):
            raise ValueError(f"{kind}: the elected candidates do not match the printed order of election")
        sheets[kind] = {"ballots": total[0], "invalid": invalid, "counts": counts, "elected": elected}
    (PARSED / "sheets.json").write_text(json.dumps(sheets))


def emit() -> Path:
    sheets = json.loads((PARSED / "sheets.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Cambridge's 1999 City Council and School Committee elections, every count from the Election "
            "Commission's certified proportional representation count sheets",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def spell(text: str) -> list[str]:
            plain = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().casefold()
            return [word for word in re.findall(r"[\w'-]+", plain) if word not in ("jr", "sr", "iii")]

        city = cs.area("us.place", "2511000")
        whole = cs.unit("us.place", "2511000")
        school = cs.key("us.body", "MA:2511000:school-committee")
        stv = cs.key("us.electoral_system", "cambridge:stv")
        election = cs.key("us.election", f"{DAY}:MA:2511000")
        if {city, whole, school, stv, election} & cs.minted:
            raise ValueError("Cambridge, its School Committee, its 1999 election, and cambridge:stv must already exist")
        people: dict[tuple[str, str], set[UUID]] = {}
        for text, person in conn.execute(
            """select coalesce(c.ballot_name, o.ballot_label), c.person_id from candidacy c
               join contest_option o on o.id = c.option_id join contest k on k.id = o.contest_id
               join election e on e.id = k.election_id join polity_territory t on t.polity_id = e.polity_id
               where t.area_id = %s""",
            [city],
        ).fetchall():
            words = spell(text)
            if words:
                people.setdefault((words[0], words[-1]), set()).add(person)

        for kind, (stamp, path, seats, existing) in SHEETS.items():
            record = sheets[kind]
            counts: dict[str, list[int]] = record["counts"]
            body = "City Council" if kind == "council" else "School Committee"
            title = f"November 2, 1999 Cambridge municipal election: {body}, general result record of the proportional representation count"
            source = cs.source(PUBLISHER, title)
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=ORIGIN.format(path),
                license="Public record",
            )
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, ORIGIN.format(path))), source)
            current: dict[str, UUID] = {}
            if existing is None:
                contest = cs.key("us.contest", f"{DAY}:MA:2511000:{kind}")
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=school,
                    district_area_id=city,
                    magnitude=seats,
                    round_number=1,
                    electoral_system_id=stv,
                    default_result_kind="counted",
                    term="full",
                )
                name(contest, "1999 Cambridge School Committee election")
                cs.add("slug", path=f"us/ma/cambridge/{DAY}-local/school-committee", entity_id=contest)
            else:
                contest = cs.key("us.contest", existing)
                if contest in cs.minted:
                    raise ValueError(f"{existing} must already exist")
                fields = ("id", "election_id", "contest_id", "stage", "kind", "status", "source_id", "as_of")
                fields += ("completeness", "supersedes_id")
                for values in conn.execute(
                    f"""select {", ".join(fields)} from result_set where contest_id = %s and is_default
                        and changeset_id not in (select id from changeset where pipeline = %s)""",
                    [contest, SOURCE],
                ).fetchall():
                    cs.add("result_set", **dict(zip(fields, values, strict=True)), is_default=False)
                    cs.add("note", entity_id=values[0], kind="caveat", body_md=DECLARED)
                for option, label in conn.execute(
                    "select id, ballot_label from contest_option where contest_id = %s and kind = 'candidate'",
                    [contest],
                ).fetchall():
                    current[spell(label)[-1]] = option

            options: dict[str, UUID] = {}
            for row in counts:
                if row == "EXHAUSTED":
                    continue
                if row in WRITE_IN:
                    options[row] = cs.key("us.option", f"{contest}:write_in:scattered")
                    if list(options.values()).count(options[row]) == 1:
                        cs.add(
                            "contest_option",
                            id=options[row],
                            contest_id=contest,
                            kind="write_in",
                            ballot_label="Write-in",
                        )
                    continue
                last, rest = (part.strip() for part in re.split(r"[,.]", row, maxsplit=1))
                label = f"{rest} {last}"
                words = spell(label)
                if existing is not None:
                    options[row] = current[words[-1]]
                    continue
                matches = people.get((words[0], words[-1]), set())
                if len(matches) == 1:
                    (person,) = matches
                else:
                    person = cs.key(f"{SOURCE}.person", slug(label))
                    cs.add("person", id=person)
                    name(person, label)
                options[row] = cs.key("us.option", f"{contest}:{person}")
                cs.add("contest_option", id=options[row], contest_id=contest, kind="candidate", ballot_label=label)
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{person}:head"),
                    person_id=person,
                    option_id=options[row],
                    role="head",
                    ballot_name=label,
                    member_of_party_id=None,
                    is_independent=False,
                    is_incumbent=None,
                )
            if existing is not None and len(set(options.values())) != len(current) + 1:
                raise ValueError(f"{kind}: the sheet's candidates do not match the contest's options")

            result = cs.key(f"{SOURCE}.result_set", f"{DAY}:{kind}")
            cs.add(
                "result_set",
                id=result,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=result, kind="methodology", body_md=METHOD)
            values: list[tuple[UUID | None, int, int, int]] = [
                (None, 3, 0, record["ballots"]),
                (None, 5, 0, record["invalid"]),
                (None, 4, 1, record["ballots"] - record["invalid"]),
            ]
            values += [(None, 21, number, votes) for number, votes in enumerate(counts["EXHAUSTED"], 1)]
            merged: dict[UUID, list[int]] = {}
            for row, option in options.items():
                merged[option] = [
                    a + b for a, b in zip(merged.get(option, [0] * len(counts[row])), counts[row], strict=True)
                ]
            winners = {options[row] for row in record["elected"]}
            for option, cells in merged.items():
                for number, votes in enumerate(cells, 1):
                    if number == 1 or cells[number - 2]:
                        values.append((option, 1, number, votes))
                for outcome in ("won", "elected") if option in winners else ("lost",) if cells[-1] else ("eliminated",):
                    cs.add("outcome", result_set_id=result, option_id=option, kind=outcome)
            for option, measure, number, votes in values:
                cs.add(
                    "result_value",
                    result_set_id=result,
                    unit_id=whole,
                    option_id=option,
                    channel=0,
                    measure=measure,
                    count_round=number,
                    flags=1 if measure in (4, 21) else 0,
                    value=votes,
                )
        return cs.write()
