import re
import subprocess
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.store import DATA, connect

SOURCE = "ie.referendums"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BOOKLET = "https://assets.gov.ie/static/documents/referendum-results-1937-2024.pdf"
PAGE = "https://www.gov.ie/en/department-of-housing-local-government-and-heritage/publications/1937-2024-referendum-results/"
PUBLISHER = "Department of Housing, Local Government and Heritage"
TITLE = "Referendum Results 1937–2024"
VOTES, REGISTERED, BALLOTS, VALID, INVALID = 1, 2, 3, 4, 5
QUESTIONS = [
    (date(1937, 7, 1), "Draft Constitution", "Draft Constitution"),
    (date(1959, 6, 17), "Voting system", "Third Amendment of the Constitution Bill, 1958"),
    (date(1968, 10, 16), "Formation of Dáil constituencies", "Third Amendment of the Constitution Bill, 1968"),
    (date(1968, 10, 16), "Voting system", "Fourth Amendment of the Constitution Bill, 1968"),
    (date(1972, 5, 10), "Accession to the European Communities", "Third Amendment of the Constitution Bill, 1971"),
    (date(1972, 12, 7), "Voting age", "Fourth Amendment of the Constitution Bill, 1972"),
    (date(1972, 12, 7), "Recognition of specified religions", "Fifth Amendment of the Constitution Bill, 1972"),
    (date(1979, 7, 5), "Adoption", "Sixth Amendment of the Constitution (Adoption) Bill, 1978"),
    (
        date(1979, 7, 5),
        "University representation in Seanad Éireann",
        (
            "Seventh Amendment of the Constitution (Election of Members of Seanad Éireann by Institutions of Higher "
            "Education) Bill, 1979"
        ),
    ),
    (date(1983, 9, 7), "Right to life of the unborn", "Eighth Amendment of the Constitution Bill, 1982"),
    (
        date(1984, 6, 14),
        "Extension of voting rights at Dáil elections",
        "Ninth Amendment of the Constitution Bill, 1984",
    ),
    (date(1986, 6, 26), "Dissolution of marriage", "Tenth Amendment of the Constitution Bill, 1986"),
    (date(1987, 5, 26), "Single European Act", "Tenth Amendment of the Constitution Bill, 1987"),
    (date(1992, 6, 18), "European Union", "Eleventh Amendment of the Constitution Bill, 1992"),
    (date(1992, 11, 25), "Right to life", "Twelfth Amendment of the Constitution Bill, 1992"),
    (date(1992, 11, 25), "Travel", "Thirteenth Amendment of the Constitution Bill, 1992"),
    (date(1992, 11, 25), "Information", "Fourteenth Amendment of the Constitution Bill, 1992"),
    (date(1995, 11, 24), "Dissolution of marriage", "Fifteenth Amendment of the Constitution (No. 2) Bill, 1995"),
    (date(1996, 11, 28), "Bail", "Sixteenth Amendment of the Constitution Bill, 1996"),
    (date(1997, 10, 30), "Cabinet confidentiality", "Seventeenth Amendment of the Constitution (No. 2) Bill, 1997"),
    (date(1998, 5, 22), "Treaty of Amsterdam", "Eighteenth Amendment of the Constitution Bill, 1998"),
    (date(1998, 5, 22), "British-Irish Agreement", "Nineteenth Amendment of the Constitution Bill, 1998"),
    (
        date(1999, 6, 11),
        "Local government",
        "Twentieth Amendment of the Constitution (No. 2) Bill, 1999",
    ),
    (
        date(2001, 6, 7),
        "Prohibition of the death penalty",
        "Twenty-first Amendment of the Constitution (No. 2) Bill, 2001",
    ),
    (date(2001, 6, 7), "International Criminal Court", "Twenty-third Amendment of the Constitution Bill, 2001"),
    (date(2001, 6, 7), "Treaty of Nice", "Twenty-fourth Amendment of the Constitution Bill, 2001"),
    (
        date(2002, 3, 6),
        "Human life in pregnancy",
        "Twenty-fifth Amendment of the Constitution (Protection of Human Life in Pregnancy) Bill, 2001",
    ),
    (date(2002, 10, 19), "Treaty of Nice", "Twenty-sixth Amendment of the Constitution Bill, 2002"),
    (date(2004, 6, 11), "Citizenship", "Twenty-seventh Amendment of the Constitution Bill, 2004"),
    (date(2008, 6, 12), "Treaty of Lisbon", "Twenty-eighth Amendment of the Constitution Bill, 2008"),
    (
        date(2009, 10, 2),
        "Treaty of Lisbon",
        "Twenty-eighth Amendment of the Constitution (Treaty of Lisbon) Bill, 2009",
    ),
    (
        date(2011, 10, 27),
        "Judges' remuneration",
        "Twenty-ninth Amendment of the Constitution (Judges' Remuneration) Bill, 2011",
    ),
    (
        date(2011, 10, 27),
        "Houses of the Oireachtas inquiries",
        "Thirtieth Amendment of the Constitution (Houses of the Oireachtas Inquiries) Bill, 2011",
    ),
    (
        date(2012, 5, 31),
        "Fiscal Stability Treaty",
        (
            "Thirtieth Amendment of the Constitution (Treaty on Stability, Coordination and Governance in the Economic "
            "and Monetary Union) Bill 2012"
        ),
    ),
    (date(2012, 11, 10), "Children", "Thirty-first Amendment of the Constitution (Children) Bill 2012"),
    (
        date(2013, 10, 4),
        "Abolition of Seanad Éireann",
        "Thirty-second Amendment of the Constitution (Abolition of Seanad Éireann) Bill 2013",
    ),
    (date(2013, 10, 4), "Court of Appeal", "Thirty-third Amendment of the Constitution (Court of Appeal) Bill 2013"),
    (
        date(2015, 5, 22),
        "Marriage equality",
        "Thirty-fourth Amendment of the Constitution (Marriage Equality) Bill 2015",
    ),
    (
        date(2015, 5, 22),
        "Age of eligibility for election to the office of President",
        (
            "Thirty-fifth Amendment of the Constitution (Age of Eligibility for Election to the Office of President) "
            "Bill 2015"
        ),
    ),
    (
        date(2018, 5, 25),
        "Regulation of termination of pregnancy",
        "Thirty-sixth Amendment of the Constitution Bill 2018",
    ),
    (
        date(2018, 10, 26),
        "Blasphemy",
        (
            "Thirty-seventh Amendment of the Constitution (Repeal of offence of publication or utterance of blasphemous "
            "matter) Bill 2018"
        ),
    ),
    (
        date(2019, 5, 24),
        "Dissolution of marriage",
        "Thirty-eighth Amendment of the Constitution (Dissolution of Marriage) Bill 2016",
    ),
    (date(2024, 3, 8), "Family", "Thirty-ninth Amendment of the Constitution (The Family) Bill 2023"),
    (date(2024, 3, 8), "Care", "Fortieth Amendment of the Constitution (Care) Bill 2023"),
]
GENERAL_ELECTIONS = [
    (date(1937, 7, 1), 1935),
    (date(1948, 2, 4), 1947),
    (date(1961, 10, 4), 1961),
    (date(1969, 6, 18), 1969),
    (date(1977, 6, 16), 1974),
    (date(1981, 6, 11), 1980),
    (date(1987, 2, 17), 1983),
    (date(1992, 11, 25), 1990),
    (date(1997, 6, 6), 1995),
    (date(2002, 5, 17), 1998),
    (date(2007, 5, 24), 2005),
    (date(2011, 2, 25), 2009),
    (date(2016, 2, 26), 2013),
    (date(2020, 2, 8), 2017),
    (date(2024, 11, 29), 2023),
]
LOCAL = {date(1979, 7, 5), date(1984, 6, 14), date(1999, 6, 11), date(2004, 6, 11), date(2019, 5, 24)}
REJECTED = {
    (date(1959, 6, 17), 1),
    (date(1968, 10, 16), 1),
    (date(1968, 10, 16), 2),
    (date(1986, 6, 26), 1),
    (date(1992, 11, 25), 1),
    (date(2001, 6, 7), 3),
    (date(2002, 3, 6), 1),
    (date(2008, 6, 12), 1),
    (date(2011, 10, 27), 2),
    (date(2013, 10, 4), 1),
    (date(2015, 5, 22), 2),
    (date(2024, 3, 8), 1),
    (date(2024, 3, 8), 2),
}
CORRECTIONS = {
    (9, "Offaly"): (
        "no",
        679,
        (
            "The booklet prints Offaly's votes against as 697. Its total poll for Offaly (11,992) and its national total "
            "against (45,484) both give 679, which is used here."
        ),
    ),
    (19, "Dublin North"): (
        "yes",
        17338,
        (
            "The booklet prints Dublin North's votes in favor as 17,388. Its total poll for Dublin North (22,520) and its "
            "national total in favor (579,740) both give 17,338, which is used here."
        ),
    ),
}
REMARKS = {
    7: "The booklet's national total of spoilt votes is printed as 49,326; its constituency rows add up to 49,226, "
    "which with the votes for and against gives its printed total poll of 903,659.",
    25: "The booklet prints Dublin South-Central's total poll as 25,624, 88 more than its votes for, against, and "
    "spoilt (25,536). The figures are kept as printed, as they are in the national total poll.",
}
CITIES = {"County Boroughs", "Borough Corporations", "Cities"}
DIVIDED = {"Cork", "Dublin", "Galway", "Limerick", "Waterford"}
RENAMED = {
    "Laoghis": "Laois",
    "Tipperary (NR)": "North Tipperary",
    "Tipperary (SR)": "South Tipperary",
    "Dun Laoghaire Rathdown": "Dún Laoghaire–Rathdown",
    "Dún Laoghaire Rathdown": "Dún Laoghaire–Rathdown",
}
ROW = re.compile(
    r"^(?P<name>.*?)\s*(?P<electorate>\d[\d,]*)\s+(?P<poll>\d[\d,]*)\s+\d+\.\d+\s+(?P<yes>\d[\d,]*)\s+"
    r"(?P<no>\d[\d,]*)\s+(?P<spoilt>\d[\d,]*)\**\s*$"
)
HEADING = re.compile(r"^\s*(\d+)\.\s+(Plebiscite|Referendum) on\b")
GROUP = re.compile(
    r"^\*?(Borough Constituencies|County Constituencies|Counties|Cities|County Boroughs|Borough Corporations|"
    r"County Councils)$"
)
POLLED = re.compile(r"(?:held on|took place on \w+,?) (\d{1,2} \w+ \d{4})")
MEASURES = ("electorate", "poll", "yes", "no", "spoilt")
METHODOLOGY = (
    "Each counting area's electorate, total poll, votes for and against the proposal, and spoilt votes as the "
    "Department of Housing, Local Government and Heritage's booklet Referendum Results 1937–2024 prints them (page "
    "{page}). Valid votes are the votes for and against."
)
SYSTEM = (
    "A Bill to amend the Constitution that has passed both Houses of the Oireachtas is put to the people, and it is "
    "approved if a majority of the votes cast on it are in favor; there is no turnout threshold (Articles 46 and 47 "
    "of the Constitution). The draft Constitution itself was put to a plebiscite on 1 July 1937 and approved by a "
    "majority of the votes cast. Votes are counted in each Dáil constituency, or in each city and county when a "
    "local or European Parliament election is held the same day, and reported to the referendum returning officer."
)
QUESTION = "Do you approve of the proposal to amend the Constitution contained in the undermentioned Bill?\n\n{bill}"


def fetch() -> None:
    raw.fetch(SOURCE, BOOKLET, suffix=".pdf")


def number(text: str) -> int:
    return int(text.replace(",", ""))


def act(day: date) -> int:
    return max(year for held, year in GENERAL_ELECTIONS if held <= day)


def local(group: str | None, label: str) -> tuple[str, str]:
    label = RENAMED.get(label, label)
    if label in DIVIDED:
        label = f"{label} City" if group in CITIES else f"{label} County"
    return slug(label), label


def parse() -> None:
    text = subprocess.run(
        ["pdftotext", "-layout", str(raw.cached(SOURCE, BOOKLET).path), "-"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    rows = []
    printed = []
    polled: dict[int, str] = {}
    section = 0
    table = False
    group = None
    for page_number, page in enumerate(text.split("\f"), 1):
        lines = page.splitlines()
        for index, line in enumerate(lines):
            heading = HEADING.match(line)
            if heading and "..." not in line and "…" not in line and (section or heading[1] == "8"):
                section += 1
                table = False
                group = None
                continue
            if not section:
                continue
            if section not in polled and (
                found := POLLED.search(re.sub(r"\s+", " ", " ".join(lines[index : index + 2])))
            ):
                polled[section] = found[1]
            if "Electorate" in line:
                table = True
                continue
            if not table:
                continue
            if found := GROUP.match(line.strip()):
                group = found[1]
                continue
            found = ROW.match(line)
            if found is None:
                continue
            label = found["name"].strip()
            if not label:
                after = lines[index + 1].strip() if index + 1 < len(lines) else ""
                label = f"{lines[index - 1].strip()} {after}"
            label = re.sub(r"\s+", " ", label).strip().lstrip("*")
            values = {key: number(found[key]) for key in MEASURES}
            if label.upper() == "TOTAL":
                printed.append({"question": section, "page": page_number, **values})
                table = False
                continue
            remark = None
            if (section, label) in CORRECTIONS:
                column, value, remark = CORRECTIONS[section, label]
                values[column] = value
            rows.append(
                {"question": section, "group": group, "label": label, "page": page_number, "remark": remark, **values}
            )
    if section != len(QUESTIONS) or len(printed) != len(QUESTIONS):
        raise ValueError(f"{section} sections and {len(printed)} totals for {len(QUESTIONS)} questions")
    for question, (day, _, _) in enumerate(QUESTIONS, 1):
        if polled.get(question, "").lstrip("0") != f"{day.day} {day:%B %Y}":
            raise ValueError(f"question {question}: the booklet gives polling day {polled.get(question)}, not {day}")
    frame = pl.DataFrame(rows, infer_schema_length=None)
    codes = []
    for question, group, label in frame.select("question", "group", "label").iter_rows():
        day = QUESTIONS[question - 1][0]
        if day in LOCAL:
            code, name = local(group, label)
            codes.append(("ie.local_authority", code, name))
        else:
            codes.append(("ie.dail", f"{act(day)}:{slug(label)}", label))
    frame = frame.with_columns(
        pl.DataFrame(codes, schema=["scheme", "code", "name"], orient="row").to_struct("unit")
    ).unnest("unit")
    duplicated = frame.filter(pl.struct("question", "code").is_duplicated())
    if duplicated.height:
        raise ValueError(f"duplicate counting areas: {duplicated}")
    totals = pl.DataFrame(printed)
    summed = frame.group_by("question").agg(pl.col(MEASURES).sum())
    check = totals.join(summed, on="question", suffix="_rows").sort("question")
    for column in MEASURES:
        for row in check.filter(pl.col(column) != pl.col(f"{column}_rows")).iter_rows(named=True):
            if row["question"] not in REMARKS:
                raise ValueError(
                    f"question {row['question']}: printed {column} {row[column]}, rows {row[f'{column}_rows']}"
                )
    unbalanced = frame.filter(pl.col("poll") != pl.col("yes") + pl.col("no") + pl.col("spoilt"))
    if set(unbalanced["question"]) - set(REMARKS):
        raise ValueError(f"unbalanced rows: {unbalanced}")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "units.parquet")
    totals.write_parquet(PARSED / "totals.parquet")
    print(frame.group_by("scheme").agg(pl.len(), pl.col("code").n_unique()))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "units.parquet")
    totals = pl.read_parquet(PARSED / "totals.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Ireland's 1937 plebiscite on the draft Constitution and its 43 constitutional referendums from 1959 "
                "to 2024 by constituency or city and county, from the Department of Housing's results booklet"
            ),
            polity="IE",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        ireland = cs.polity("IE")
        nation = cs.area("ie.nation", "IE")
        if nation in cs.minted:
            raise ValueError("publish ie.returning_officer before the referendums")
        if conn.execute("select 1 from scheme where code = 'ie.local_authority'").fetchone() is None:
            cs.add(
                "scheme",
                code="ie.local_authority",
                polity_id=ireland,
                kind="administrative",
                rank=2,
                noun="city or county",
            )
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url=PAGE,
            license="Creative Commons Attribution 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            attribution=(
                "Referendum Results 1937–2024, Department of Housing, Local Government and Heritage (gov.ie), "
                "reused under CC BY 4.0."
            ),
        )
        cs.artifact(raw.cached(SOURCE, BOOKLET), source)
        system = cs.key("ie.electoral_system", "constitutional-referendum")
        cs.add("electoral_system", id=system, polity_id=ireland, rules={"formula": "referendum"}, rules_version=1)
        name(system, "Irish constitutional referendums")
        cs.add("note", entity_id=system, kind="methodology", body_md=SYSTEM)

        units: dict[tuple[str, str], UUID] = {}
        for scheme, code, label in frame.select("scheme", "code", "name").unique().sort("scheme", "code").iter_rows():
            area = cs.area(scheme, code)
            if area in cs.minted:
                cs.add("area", id=area, scheme_code=scheme)
                cs.add("area_code", area_id=area, code=code, valid_from=None)
                cs.add("area_parent", area_id=area, parent_id=nation, valid_from=None)
                name(area, label)
            unit = cs.unit(scheme, code)
            if unit in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=ireland,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                name(unit, label)
            units[scheme, code] = unit

        values = []
        days = sorted({day for day, _, _ in QUESTIONS})
        for day in days:
            questions = [(n, q) for n, q in enumerate(QUESTIONS, 1) if q[0] == day]
            election = cs.key("ie.election", f"{day}:referendum")
            cs.add(
                "election", id=election, polity_id=ireland, kind="referendum", date_from=day, date_to=day, status="held"
            )
            plural = "s" if len(questions) > 1 else ""
            kind = "plebiscite" if day.year == 1937 else "referendum"
            name(election, f"{day:%B} {day.year} Irish constitutional {kind}{plural}")
            path = f"ie/{day}-{kind}"
            cs.add("slug", path=path, entity_id=election)
            for position, (question, (_, subject, bill)) in enumerate(questions, 1):
                rows = frame.filter(pl.col("question") == question)
                printed = totals.filter(pl.col("question") == question).row(0, named=True)
                contest_key = f"{day}:referendum:{position}"
                contest = cs.key("ie.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    electoral_system_id=system,
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add(
                    "ballot_question",
                    contest_id=contest,
                    type="plebiscite" if kind == "plebiscite" else "mandatory_referendum",
                )
                name(contest, subject)
                cs.add("slug", path=f"{path}/{slug(subject)}", entity_id=contest)
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1fen"),
                    contest_id=contest,
                    kind="text" if kind == "referendum" else "summary",
                    lang="en",
                    body=QUESTION.format(bill=bill) if kind == "referendum" else "Plebiscite on the Draft Constitution",
                    url=BOOKLET,
                    source_id=source,
                    locator={"page": rows["page"].min()},
                )
                options = {}
                for answer, label in ((1, "Yes"), (2, "No")):
                    options[answer] = cs.key("ie.option", f"{contest_key}:{label.lower()}")
                    cs.add(
                        "contest_option",
                        id=options[answer],
                        contest_id=contest,
                        kind="answer",
                        ballot_position=answer,
                        ballot_label=label,
                    )
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
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
                remarks = [
                    METHODOLOGY.format(page=printed["page"]),
                    *rows["remark"].drop_nulls(),
                    REMARKS.get(question),
                ]
                cs.add("note", entity_id=result_set, kind="methodology", body_md=" ".join(r for r in remarks if r))
                approved = printed["yes"] > printed["no"]
                if approved == ((day, position) in REJECTED):
                    raise ValueError(f"{day} question {position}: the count disagrees with the declared result")
                for answer, option in options.items():
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=option,
                        kind="won" if approved == (answer == 1) else "lost",
                    )
                for row in rows.iter_rows(named=True):
                    unit = str(units[row["scheme"], row["code"]])
                    for option, measure, value in (
                        (options[1], VOTES, row["yes"]),
                        (options[2], VOTES, row["no"]),
                        (None, REGISTERED, row["electorate"]),
                        (None, BALLOTS, row["poll"]),
                        (None, VALID, row["yes"] + row["no"]),
                        (None, INVALID, row["spoilt"]),
                    ):
                        values.append((str(result_set), unit, None if option is None else str(option), measure, value))
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={
                    "result_set_id": pl.String,
                    "unit_id": pl.String,
                    "option_id": pl.String,
                    "measure": pl.Int16,
                    "value": pl.Int64,
                },
                orient="row",
            ).with_columns(channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
        )
        return cs.write()
