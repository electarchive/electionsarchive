import re
import subprocess
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ea import raw
from ea.changeset import Changeset
from ea.store import connect

SOURCE = "us.as_precincts"
VERSION = "1"
PUBLISHER = "American Samoa Election Office"
CLOUD = "https://dehayf5mhw1h7.cloudfront.net/wp-content/uploads/sites/594/"
ROUND_ONE = "https://dehayf5mhw1h7.cloudfront.net/wp-content/uploads/sites/594/2024/11/06063739/2024-governor-lt-governor-tally.webp"
ROUND_ONE_ROWS = {
    "central": (3, 2, 4),
    "d01": (170, 105, 256),
    "d02": (118, 69, 139),
    "d03": (235, 220, 67),
    "d04": (183, 159, 46),
    "d05": (158, 190, 82),
    "d06": (162, 85, 45),
    "d07": (122, 129, 76),
    "d08": (119, 225, 46),
    "d09": (200, 472, 30),
    "d10": (69, 97, 39),
    "d11": (100, 226, 17),
    "d12": (427, 379, 453),
    "d13": (235, 356, 112),
    "d14": (241, 119, 53),
    "d15": (682, 1032, 491),
    "d16": (316, 261, 102),
    "d17": (120, 158, 111),
}
FILES = [
    (
        date(2016, 11, 8),
        "house:0",
        "https://aceproject.org/ero-en/misc/american-samoa-general-election-2016-results-1/at_download/file",
        ["amata", "chapman", "jamias", "jones", "finau"],
    ),
    (
        date(2016, 11, 8),
        "governor",
        "https://web.archive.org/web/20161116104445/http://www.americansamoaelectionoffice.org/results/General%20Election%202016%20RESULTS_GOV_LT%20GOV.pdf",
        ["sunia", "moliga", "tuika"],
    ),
    (
        date(2020, 11, 3),
        "governor",
        CLOUD + "2020/11/12144326/2020-GENERAL-ELECTION-RESULTS-GOVERNOR-LT.pdf",
        ["tofau", "talia", "lemanu", "nua"],
    ),
    (
        date(2020, 11, 3),
        "house:0",
        CLOUD + "2020/11/12144400/2020-GENERAL-ELECTION-RESULTS-DELEGATE-TO-THE-US-CONGESS.pdf",
        ["amata", "chapman", "crichton"],
    ),
    (
        date(2024, 11, 5),
        "house:0",
        CLOUD + "2024/11/13203015/2024-congress-tally.pdf",
        ["amata", "lancaster", "kuaea", "chapman"],
    ),
    (
        date(2024, 11, 19),
        "governor",
        CLOUD + "2024/11/20193857/2024-runoff-election-territorial-summary.pdf",
        ["lemanu", "pula"],
    ),
]


def fetch() -> None:
    for _, _, url, _ in FILES:
        try:
            raw.cached(SOURCE, url)
        except LookupError:
            body = subprocess.run(["curl", "-skL", "-m", "120", url], capture_output=True, check=True).stdout
            raw.store(SOURCE, url, body, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")
    try:
        raw.cached(SOURCE, ROUND_ONE)
    except LookupError:
        body = subprocess.run(["curl", "-skL", "-m", "120", ROUND_ONE], capture_output=True, check=True).stdout
        raw.store(SOURCE, ROUND_ONE, body, suffix=".webp", retrieved_at=datetime.now(UTC), media_type="image/webp")


def emit() -> Path:
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="American Samoa's governor and delegate elections 2016, 2020, and 2024 by polling district from the Election Office, with the 2024 governor's two rounds",
            polity="US-AS",
        )
        (state,) = conn.execute("select id from polity where iso_code = 'US-AS'").fetchone()
        parent, area = conn.execute(
            "select id, area_id from reporting_unit where scheme_code = 'us.state' and polity_id = %s", [state]
        ).fetchone()
        office, system_one, _general = conn.execute(
            """select c.office_id, c.electoral_system_id, c.election_id from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = '2020-11-03:AS:governor'"""
        ).fetchone()
        (system_two,) = conn.execute(
            """select c.electoral_system_id from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = '2012-11-20:AS:governor'"""
        ).fetchone()
        (general_24,) = conn.execute(
            """select c.election_id from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = '2024-11-05:AS:house:0'"""
        ).fetchone()
        persons = {
            "lemanu": conn.execute(
                "select p.id from person p join name n on n.entity_id = p.id and n.text = 'Lemanu Palepoi Mauga'"
            ).fetchone()[0],
            "vaitautolu": conn.execute(
                "select p.id from person p join name n on n.entity_id = p.id and n.text = %s",
                ["I’aulualo Fa’afetai Talia"],
            ).fetchone()[0],
        }
        persons["pula"] = cs.key(f"{SOURCE}.person", "pula-nikolao-pula")
        cs.add("person", id=persons["pula"])
        cs.add(
            "name", entity_id=persons["pula"], text="Pula Nikolao Pula", lang="en", kind="official", is_preferred=True
        )
        runoff_election = cs.key("us.election", "2024-11-19:AS")
        cs.add(
            "election",
            id=runoff_election,
            polity_id=state,
            kind="runoff",
            date_from=date(2024, 11, 19),
            date_to=date(2024, 11, 19),
            date_precision="day",
            status="held",
        )
        cs.add(
            "name",
            entity_id=runoff_election,
            text="2024 American Samoa runoff election",
            lang="en",
            kind="official",
            is_preferred=True,
        )
        cs.add("slug", path="us/as/2024-11-19-runoff", entity_id=runoff_election)
        sources = {}
        results: dict[tuple[date, str], dict[str, dict[str, int]]] = {}
        for day, section, url, keywords in FILES:
            artifact = raw.cached(SOURCE, url)
            text = subprocess.run(
                ["pdftotext", "-layout", str(artifact.path), "-"], capture_output=True, check=True, text=True
            ).stdout
            title = f"{day.year} {'runoff' if day.month == 11 and day.day == 19 else 'general'} election results by polling district"
            if (day, section) not in sources:
                sources[(day, section)] = cs.source(PUBLISHER, title + f" ({section})")
                cs.add(
                    "source",
                    id=sources[(day, section)],
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=title + f" ({section})",
                    url="https://aselectionoffice.gov/",
                    license="Public record",
                )
                cs.artifact(artifact, sources[(day, section)])
            for line in text.splitlines():
                found = re.match(
                    r"^(?:(?P<label>[A-Za-z].*?)\s{2,})?(?P<nums>\d[\d,]*(?:\s+\d[\d,]*)*)\s*$", line.strip()
                )
                if found is None:
                    continue
                numbers = [int(number.replace(",", "")) for number in found["nums"].split()]
                if len(numbers) != len(keywords) + 1 or (found["label"] or "").startswith("Total"):
                    continue
                district = re.match(r"Dist #(\d+)", found["label"] or "")
                code = f"{day}:AS:{'d' + district[1].zfill(2) if district else 'central'}"
                results.setdefault((day, section), {})[code] = dict(zip(keywords, numbers[:-1], strict=True))
            if "Island" in text and not any(code.endswith("central") for code in results[(day, section)]):
                raise ValueError(f"{day} {section}: no central office row")
        round_one = cs.key("us.contest", "2024-11-05:AS:governor")
        round_two = cs.key("us.contest", "2024-11-19:AS:governor")
        for contest, election, round_number, previous, slug in (
            (round_one, general_24, 1, None, "us/as/2024-11-05-general/governor"),
            (round_two, runoff_election, 2, round_one, "us/as/2024-11-19-runoff/governor"),
        ):
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=office,
                district_area_id=area,
                term="full",
                magnitude=1,
                round_number=round_number,
                previous_round_id=previous,
                electoral_system_id=system_two if round_number == 2 else system_one,
                default_result_kind="counted",
            )
            cs.add(
                "name",
                entity_id=contest,
                text=f"2024 American Samoa governor election, round {round_number}",
                lang="en",
                kind="official",
                is_preferred=True,
            )
            cs.add("slug", path=slug, entity_id=contest)
        labels = {
            "pula": "Pula Nikolao Pula",
            "lemanu": "Lemanu Palepoi Mauga",
            "vaitautolu": "Vaitautolu Talia Iaulualo",
        }
        options = {}
        for contest, names in ((round_one, ("pula", "lemanu", "vaitautolu")), (round_two, ("pula", "lemanu"))):
            for name in names:
                option = cs.key("us.option", f"{contest}:{name}")
                options[(contest, name)] = option
                cs.add("contest_option", id=option, contest_id=contest, kind="candidate", ballot_label=labels[name])
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{contest}:{name}"),
                    person_id=persons[name],
                    option_id=option,
                    role="head",
                    is_independent=True,
                )
        for column, total in enumerate((3660, 4284, 2169)):
            if sum(row[column] for row in ROUND_ONE_ROWS.values()) != total:
                raise ValueError("round one transcription does not tie to the certified totals")
        title = "2024 general election results by polling district (governor, election-night sheet)"
        sources[(date(2024, 11, 5), "governor")] = cs.source(PUBLISHER, title)
        cs.add(
            "source",
            id=sources[(date(2024, 11, 5), "governor")],
            kind="official_body",
            publisher=PUBLISHER,
            title=title,
            url=ROUND_ONE,
            license="Public record",
        )
        cs.artifact(raw.cached(SOURCE, ROUND_ONE), sources[(date(2024, 11, 5), "governor")])
        results[(date(2024, 11, 5), "governor")] = {
            f"{date(2024, 11, 5)}:AS:{code}": dict(zip(("lemanu", "pula", "vaitautolu"), row, strict=True))
            for code, row in ROUND_ONE_ROWS.items()
        }
        units = {}
        for (day, section), districts in results.items():
            key = f"{day}:AS:{section}"
            if section == "governor" and day == date(2024, 11, 19):
                contest, election = round_two, runoff_election
            elif section == "governor" and day == date(2024, 11, 5):
                contest, election = round_one, general_24
            else:
                contest, election = conn.execute(
                    "select c.id, c.election_id from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest' and x.value = %s",
                    [key],
                ).fetchone()
            existing = conn.execute(
                "select lower(ballot_label), id from contest_option where contest_id = %s", [contest]
            ).fetchall()
            picked = {}
            for keyword in {keyword for votes in districts.values() for keyword in votes}:
                found = (
                    [option for label, option in existing if keyword in label]
                    if existing
                    else [options[(contest, keyword)]]
                )
                if len(found) != 1:
                    raise ValueError(f"{key}: {len(found)} options for {keyword}")
                picked[keyword] = found[0]
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:district")
            columns = (
                "id",
                "election_id",
                "contest_id",
                "stage",
                "kind",
                "status",
                "source_id",
                "as_of",
                "completeness",
                "supersedes_id",
            )
            for prior in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default and id <> %s",
                [contest, result_set],
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, prior, strict=True)), is_default=False)
            ranked = sorted(picked, key=lambda keyword: -sum(votes[keyword] for votes in districts.values()))
            if existing:
                for option, outcome_kind, seats in conn.execute(
                    """select o.option_id, o.kind, o.seats from outcome o
                       join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s and rs.id <> %s""",
                    [contest, result_set],
                ).fetchall():
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=outcome_kind, seats=seats)
                official = conn.execute(
                    "select sum(v.value) from result_set r join result_value v on v.result_set_k = r.k where r.contest_id = %s and r.is_default and v.measure = 1",
                    [contest],
                ).fetchone()[0]
                counted = sum(count for votes in districts.values() for count in votes.values())
                completeness = round(float(counted) / float(official), 4)
            else:
                if contest == round_two:
                    cs.add("outcome", result_set_id=result_set, option_id=picked[ranked[0]], kind="elected", seats=None)
                completeness = 1
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[(day, section)],
                is_default=True,
                completeness=completeness,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md="Votes by polling district from the Election Office's results sheets; the central office row holds Swains Island and, where the sheet says so, absentee ballots."
                + (
                    " The first-round sheet is an image published as the election-night unofficial results; its figures were transcribed by hand and each column sums to the totals the Office certified on November 12, 2024 (reported by Talanei)."
                    if key == "2024-11-05:AS:governor"
                    else ""
                ),
            )
            for code, votes in districts.items():
                if code not in units:
                    units[code] = cs.unit("us.precinct", code)
                    cs.add(
                        "reporting_unit",
                        id=units[code],
                        polity_id=state,
                        scheme_code="us.precinct",
                        code=code,
                        parent_id=parent,
                        area_relation="within",
                        area_id=area,
                        kind="standard",
                        valid_from=day,
                        valid_to=day + timedelta(days=1),
                    )
                for keyword, count in votes.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=units[code],
                        option_id=picked[keyword],
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=count,
                    )
        return cs.write()
