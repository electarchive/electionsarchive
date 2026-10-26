import html
import io
import json
import re
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "it.referendum"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OPENDATA = "https://dait.interno.gov.it/documenti/opendata/referendum/referendum-{day:%Y%m%d}.zip"
PAGE = (
    "https://elezionistorico.interno.gov.it/index.php?tpel=F&dtel={day:%d/%m/%Y}&tpa={area}&tpe=A&lev0=0&levsut0=0"
    "&es0=S&ms=S"
)
INBOX = DATA / "inbox" / "manual" / "eligendo-referendum" / "pages.json"
REGISTERED, VALID, INVALID, BLANK, VOTES, VOTERS = 2, 4, 5, 6, 1, 14
ABROAD = date(2003, 1, 1)
HEADERLESS = [
    "REGIONE",
    "PROVINCIA",
    "COMUNE",
    "NUM_REFERENDUM",
    "QUESITO",
    "ELETTORI",
    "ELETTORI_MASCHI",
    "VOTANTI",
    "VOTANTI_MASCHI",
    "NUMVOTISI",
    "NUMVOTINO",
    "SCHEDE_BIANCHE",
]
ONE_DAY = {date(2016, 4, 17), date(2016, 12, 4), date(2022, 6, 12)}
CONSTITUTIONAL = {date(2001, 10, 7), date(2006, 6, 25), date(2016, 12, 4), date(2020, 9, 20), date(2026, 3, 22)}
CONSULTATIVE = {date(1989, 6, 18)}
REGIONS = {
    "PIEMO": "01",
    "VALLE": "02",
    "LOMBA": "03",
    "TRENT": "04",
    "VENET": "05",
    "FRIUL": "06",
    "LIGUR": "07",
    "EMILI": "08",
    "TOSCA": "09",
    "UMBRI": "10",
    "MARCH": "11",
    "LAZIO": "12",
    "ABRUZ": "13",
    "MOLIS": "14",
    "CAMPA": "15",
    "PUGLI": "16",
    "BASIL": "17",
    "CALAB": "18",
    "SICIL": "19",
    "SARDE": "20",
}
QUESTIONS = {
    date(1974, 5, 12): ["Divorce law: repeal"],
    date(1978, 6, 11): ["Public order (Reale law): repeal", "Public funding of parties: repeal"],
    date(1981, 5, 17): [
        "Public order (Cossiga law): repeal",
        "Life imprisonment: repeal",
        "Gun licenses: repeal",
        "Abortion: Radical Party's broader repeal",
        "Abortion: Movement for Life's restrictive repeal",
    ],
    date(1985, 6, 9): ["Wage indexation cut (scala mobile): repeal"],
    date(1987, 11, 8): [
        "Civil liability of judges",
        "Parliament's commission for ministerial offenses",
        "Siting of nuclear power plants",
        "Grants to local authorities hosting power plants",
        "ENEL's participation in nuclear plants abroad",
    ],
    date(1989, 6, 18): ["Constituent mandate for the European Parliament (consultative)"],
    date(1990, 6, 3): ["Hunting rules", "Hunters' access to private land", "Use of pesticides"],
    date(1991, 6, 9): ["Single preference vote for the Chamber of Deputies"],
    date(1993, 4, 18): [
        "Environmental controls by local health units",
        "Personal drug use",
        "Public funding of parties",
        "Savings banks' appointments",
        "Ministry of State Holdings",
        "Senate electoral law",
        "Ministry of Agriculture and Forests",
        "Ministry of Tourism and Entertainment",
    ],
    date(1995, 6, 11): [
        "Workplace union representation (broad)",
        "Workplace union representation (narrow)",
        "Public sector bargaining",
        "Internal exile for suspected mafiosi",
        "Privatization of RAI",
        "Retail trade licenses",
        "Payroll deduction of union dues",
        "Municipal electoral law",
        "Shop opening hours",
        "National television licenses",
        "Advertising breaks in films",
        "Advertising sales by broadcasters",
    ],
    date(1997, 6, 15): [
        "Golden share in privatized companies",
        "Conscientious objection",
        "Hunters' access to private land",
        "Careers of magistrates",
        "Order of Journalists",
        "Magistrates' outside appointments",
        "Ministry of Agricultural Policy",
    ],
    date(1999, 4, 18): ["Chamber of Deputies: abolition of the proportional list vote"],
    date(2000, 5, 21): [
        "Reimbursement of election and referendum expenses",
        "Chamber of Deputies: abolition of the proportional list vote",
        "Election of the High Council of the Judiciary",
        "Separation of judges' and prosecutors' careers",
        "Magistrates' outside appointments",
        "Reinstatement after unfair dismissal",
        "Payroll deduction of union dues by social security bodies",
    ],
    date(2001, 10, 7): ["Reform of Title V of the Constitution (regions and local authorities)"],
    date(2003, 6, 15): ["Reinstatement after unfair dismissal in small firms", "Compulsory easements for power lines"],
    date(2005, 6, 12): [
        "Assisted reproduction: limits on embryo research",
        "Assisted reproduction: limits on access",
        "Assisted reproduction: aims and rights of those involved",
        "Assisted reproduction: ban on donor gametes",
    ],
    date(2006, 6, 25): ["Reform of Part II of the Constitution"],
    date(2009, 6, 21): [
        "Chamber of Deputies: majority bonus to a coalition",
        "Senate: majority bonus to a coalition",
        "Chamber of Deputies: candidacy in several constituencies",
    ],
    date(2011, 6, 12): [
        "Local public services: award and management",
        "Water tariffs: return on invested capital",
        "Nuclear power",
        "Legitimate impediment of the Prime Minister and ministers",
    ],
    date(2016, 4, 17): ["Duration of offshore drilling licenses"],
    date(2016, 12, 4): ["Renzi–Boschi constitutional reform"],
    date(2020, 9, 20): ["Reduction in the number of members of Parliament"],
    date(2022, 6, 12): [
        "Ineligibility after a criminal conviction (Severino law)",
        "Limits on pretrial detention",
        "Separation of judges' and prosecutors' functions",
        "Lay members' votes in judicial councils",
        "Election of the High Council of the Judiciary's judicial members",
    ],
    date(2026, 3, 22): ["Judicial system and a High Disciplinary Court"],
}
OUTCOME = (
    "Turnout was {turnout} of registered voters{abroad}, short of the quorum of more than half. Yes won {yes} of "
    "valid votes, so the law stays in force."
)
METHODOLOGY = (
    "Regions are summed from the Interior Ministry's open data files, which give each municipality's or province's "
    "electors, voters, blank ballots, and Yes and No votes. Invalid ballots, here including contested ones, are voters "
    "less valid and blank ballots. Votes abroad, counted since 2003, are the Ministry's national totals for the "
    "foreign constituency from its Eligendo archive, and Italy's totals match Eligendo's national figures."
)
TURNOUT = (
    "Italy's registered voters and voters on the day, as the Interior Ministry's Eligendo archive gives them for the "
    "first question. The count is the one the quorum is tested against; later questions' ballots can differ by a few "
    "thousand and sit on each question's own result set."
)
NATIONAL = (
    "Italy's totals are the Interior Ministry's national figures from its Eligendo archive, with votes abroad, "
    "counted since 2003, as the foreign constituency's total. The Ministry's open data file for this question does "
    "not add up to them, so its regional figures are a separate, non-default set. Invalid ballots, here including "
    "contested ones, are voters less valid and blank ballots."
)
FILES = (
    "Regions summed from the Interior Ministry's open data file, which misses or misstates some municipalities or "
    "provinces for this question, so the regions do not add up to the Ministry's national totals. Invalid ballots "
    "are left out where the file's figures make them negative."
)
CONSTITUTIONAL_METHODOLOGY = (
    "A constitutional referendum confirms an amendment that Parliament passed without a two-thirds majority. It "
    "needs no quorum: the amendment takes effect if Yes wins a majority of valid votes (art. 138 of the Constitution)."
)
CONSULTATIVE_METHODOLOGY = (
    "A one-off advisory referendum held under constitutional law 2/1989. It needed no quorum and bound no one."
)


def pages() -> dict[str, str]:
    return json.loads(INBOX.read_text())


def fetch() -> None:
    for day in QUESTIONS:
        raw.fetch(SOURCE, OPENDATA.format(day=day), suffix=".zip")
    saved = pages()
    for day in QUESTIONS:
        for area in "IEY" if day >= ABROAD else "I":
            url = PAGE.format(day=day, area=area)
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.store(
                    SOURCE,
                    url,
                    saved[url.removeprefix("https://elezionistorico.interno.gov.it")].encode(),
                    suffix=".html",
                    retrieved_at=datetime(2026, 10, 24, tzinfo=UTC),
                    media_type="text/html",
                )


def totals(day: date, area: str) -> pl.DataFrame:
    text = raw.cached(SOURCE, PAGE.format(day=day, area=area)).path.read_text()
    text = re.sub(
        r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", re.sub(r"<script.*?</script>", "", text, flags=re.DOTALL)))
    )
    registered = int(re.search(r"Elettori ([\d.]+)", text)[1].replace(".", ""))
    rows = [
        (int(found[1]), found[2], registered, *(int(value.replace(".", "")) for value in found.groups()[2:]))
        for found in re.finditer(
            r"(\d+)\. (.*?) Affluenza Votanti ([\d.]+) % [\d,]+ Schede Valide ([\d.]+) Schede bianche ([\d.]+) "
            r"Schede non valide \(bianche incl\.\) ([\d.]+) SI NO ([\d.]+) ([\d.]+)",
            text[text.find("Elettori") :],
        )
    ]
    if len(rows) != len(QUESTIONS[day]):
        raise ValueError(f"{day} {area}: {len(rows)} questions on the Eligendo page")
    return pl.DataFrame(
        rows,
        schema=["question", "text", "registered", "voters", "valid", "blank", "spoiled", "yes", "no"],
        orient="row",
    ).drop("spoiled")


def parse() -> None:
    frames = []
    natives = []
    for day in QUESTIONS:
        archive = zipfile.ZipFile(raw.cached(SOURCE, OPENDATA.format(day=day)).path)
        if day == date(2026, 3, 22):
            sheet = pl.read_excel(io.BytesIO(archive.read("Referendum2026_OpenData_LivComune.xlsx")))
            sheet = sheet.slice(1).rename(dict(zip(sheet.columns, sheet.row(0), strict=True)))
            frame = sheet.select(
                region="REG",
                question=pl.lit(1),
                registered=pl.col("ELETTORITOTALI").cast(pl.Int64),
                voters=pl.col("NUMVOTANTITOTALI").cast(pl.Int64),
                yes=pl.col("VOTISI").cast(pl.Int64),
                no=pl.col("VOTIVALIDI").cast(pl.Int64) - pl.col("VOTISI").cast(pl.Int64),
                blank=pl.col("SKBIANCHE").cast(pl.Int64),
            )
        elif day == date(2022, 6, 12):
            frame = pl.concat(
                pl.read_csv(
                    io.BytesIO(archive.read(member)), separator=";", infer_schema_length=0, encoding="utf8-lossy"
                ).select(
                    region="REGIONE",
                    question=pl.col("NUM_REFERENDUM").cast(pl.Int64),
                    registered=pl.col("ELETTORI").cast(pl.Int64),
                    voters=pl.col("VOTANTI").cast(pl.Int64),
                    yes=pl.col("VOTI_SI").cast(pl.Int64),
                    no=pl.col("VOTI_NO").cast(pl.Int64),
                    blank=pl.col("SCHEDE_BIANCHE").cast(pl.Int64),
                )
                for member in archive.namelist()
                if "Scrutini_quesito" in member
            )
        else:
            (member,) = archive.namelist()
            text = archive.read(member).decode("latin-1")
            headed = text.startswith('"REGIONE"')
            frame = (
                pl.read_csv(
                    io.BytesIO(text.encode()),
                    separator=";",
                    infer_schema_length=0,
                    has_header=headed,
                    new_columns=None if headed else HEADERLESS,
                )
                .with_columns(pl.all().str.strip_chars().str.replace(r",0+$", ""))
                .select(
                    region="REGIONE",
                    question=pl.col("NUM_REFERENDUM").replace({"A": "10", "D": "11", "E": "12"}).cast(pl.Int64),
                    registered=pl.col("ELETTORI").cast(pl.Int64),
                    voters=pl.col("VOTANTI").cast(pl.Int64),
                    yes=pl.col("NUMVOTISI").cast(pl.Int64),
                    no=pl.col("NUMVOTINO").cast(pl.Int64),
                    blank=pl.col("^(SCHEDE_BIANCHE|SKBIANCHE)$").cast(pl.Int64),
                )
            )
        frame = (
            frame.with_columns(
                region=pl.col("region")
                .str.to_uppercase()
                .str.replace_all(r"[^A-Z]", "")
                .str.slice(0, 5)
                .replace_strict(REGIONS),
                day=pl.lit(day),
            )
            .group_by("day", "question", "region")
            .agg(pl.col("registered", "voters", "yes", "no", "blank").sum())
            .with_columns(valid=pl.col("yes") + pl.col("no"))
        )
        italy = totals(day, "I")
        natives.append(italy.select("question", "text").with_columns(day=pl.lit(day)))
        measures = ["registered", "voters", "valid", "blank", "yes", "no"]
        summed = frame.group_by("question").agg(pl.col(measures).sum())
        matched = italy.join(summed, on="question", suffix="_files").select(
            "question", matched=pl.all_horizontal(pl.col(column) == pl.col(f"{column}_files") for column in measures)
        )
        frames.append(frame.join(matched, on="question"))
        frames.append(italy.select("question", *measures).with_columns(day=pl.lit(day), region=pl.lit("IT")))
        if day >= ABROAD:
            abroad = totals(day, "E")
            both = totals(day, "Y")
            for column in measures:
                if (italy[column] + abroad[column] != both[column]).any():
                    raise ValueError(f"{day}: Italy and abroad do not add up to Eligendo's total {column}")
            frames.append(abroad.select("question", *measures).with_columns(day=pl.lit(day), region=pl.lit("ESTERO")))
    frame = pl.concat(frames, how="diagonal_relaxed").with_columns(
        invalid=pl.col("voters") - pl.col("valid") - pl.col("blank")
    )
    frame = frame.with_columns(
        matched=pl.col("matched") & (pl.col("invalid") >= 0).all().over("day", "question", pl.col("matched").is_null()),
        invalid=pl.when(pl.col("invalid") >= 0).then("invalid"),
    )
    if frame.filter(pl.col("matched").is_null())["invalid"].null_count():
        raise ValueError("negative invalid ballots in Eligendo's totals")
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.sort("day", "question", "region").write_parquet(PARSED / "regions.parquet")
    print(
        frame.filter(pl.col("matched").is_not_null())
        .select("day", "question", "matched")
        .unique()
        .group_by("matched")
        .len()
    )
    pl.concat(natives).write_parquet(PARSED / "questions.parquet")


def emit() -> Path:
    regions = pl.read_parquet(PARSED / "regions.parquet")
    natives = {(day, question): text for question, text, day in pl.read_parquet(PARSED / "questions.parquet").rows()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=(
                "Italy's referendums from 1974 to 2022 and the 2026 constitutional referendum by region, with votes "
                "abroad from 2003, from the Interior Ministry's open data and Eligendo archive"
            ),
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        italy = cs.polity("IT")
        interno = cs.source("Ministero dell'Interno", "Eligendo: archivio storico delle elezioni, open data")
        for day in QUESTIONS:
            cs.artifact(raw.cached(SOURCE, OPENDATA.format(day=day)), interno)
            for area in "IEY" if day >= ABROAD else "I":
                cs.artifact(raw.cached(SOURCE, PAGE.format(day=day, area=area)), interno)
        systems = {"abrogative": cs.key("it.electoral_system", "abrogative-referendum")}
        for code, label, methodology in (
            ("constitutional", "Italian constitutional referendum", CONSTITUTIONAL_METHODOLOGY),
            ("consultative", "Italian consultative referendum of 1989", CONSULTATIVE_METHODOLOGY),
        ):
            systems[code] = cs.key("it.electoral_system", f"{code}-referendum")
            cs.add(
                "electoral_system", id=systems[code], polity_id=italy, rules={"formula": "referendum"}, rules_version=1
            )
            name(systems[code], label)
            cs.add("note", entity_id=systems[code], kind="methodology", body_md=methodology)
        units = {code: cs.unit("it.regione", code) for code in REGIONS.values()}
        abroad = cs.unit("it.estero", "TOTALE")
        if abroad in cs.minted:
            cs.add(
                "reporting_unit",
                id=abroad,
                polity_id=italy,
                scheme_code="it.estero",
                code="TOTALE",
                area_relation="unlocated",
                kind="abroad_mission",
            )
            name(abroad, "Estero", "it")
            name(abroad, "Abroad")
        nation = cs.unit("it.nation", "IT")
        if nation in cs.minted:
            cs.add(
                "reporting_unit",
                id=nation,
                polity_id=italy,
                scheme_code="it.nation",
                code="IT",
                area_relation="equals",
                area_id=cs.area("it.nation", "IT"),
                kind="aggregate",
            )
            name(nation, "Italia", "it")
            name(nation, "Italy")
        values = []
        for day, questions in QUESTIONS.items():
            kind = (
                "constitutional" if day in CONSTITUTIONAL else "consultative" if day in CONSULTATIVE else "abrogative"
            )
            election = cs.key("it.election", f"{day}:referendum")
            cs.add(
                "election",
                id=election,
                polity_id=italy,
                kind="referendum",
                date_from=day,
                date_to=day if day in ONE_DAY else day + timedelta(days=1),
                status="held",
            )
            month = f"{day:%B} " if day.year == 2016 else ""
            plural = len(questions) > 1
            name(election, f"{month}{day.year} Italian {kind} referendum{'s' if plural else ''}")
            italian = {
                "constitutional": "costituzionale",
                "consultative": "consultivo",
                "abrogative": "abrogativi" if plural else "abrogativo",
            }[kind]
            name(election, f"Referendum {italian} del {day.year}", "it", "native")
            path = f"it/{day}-referendum"
            cs.add("slug", path=path, entity_id=election)
            rows = regions.filter(pl.col("day") == day)
            for number, english in enumerate(questions, 1):
                contest_key = f"{day}:referendum:{number}"
                contest = cs.key("it.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    electoral_system_id=systems[kind],
                    default_result_kind="counted",
                )
                cs.add(
                    "ballot_question",
                    contest_id=contest,
                    type="plebiscite" if kind == "consultative" else "optional_referendum",
                )
                name(contest, english)
                name(contest, natives[day, number], "it", "native")
                cs.add("slug", path=f"{path}/question-{number}", entity_id=contest)
                options = {}
                for position, (answer, label) in enumerate((("yes", "Sì"), ("no", "No")), 1):
                    options[answer] = cs.key("it.option", f"{contest_key}:{answer}")
                    cs.add(
                        "contest_option",
                        id=options[answer],
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position,
                        ballot_label=label,
                    )
                question = rows.filter(pl.col("question") == number)
                files = question.filter(pl.col("matched").is_not_null())
                eligendo = question.filter(pl.col("matched").is_null())
                regional = files["matched"].all()
                abroad_rows = eligendo.filter(pl.col("region") == "ESTERO")
                sets = {
                    contest_key: (
                        pl.concat([files, abroad_rows]) if regional else eligendo,
                        1,
                        METHODOLOGY if regional else NATIONAL,
                    )
                }
                if not regional:
                    italy_valid = eligendo.filter(pl.col("region") == "IT")["valid"][0]
                    sets[f"{contest_key}:regions"] = (
                        files,
                        min(1, round(files["valid"].sum() / italy_valid, 4)),
                        FILES,
                    )
                for set_key, (rows_in, completeness, note) in sets.items():
                    result_set = cs.key(f"{SOURCE}.result_set", set_key)
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="preliminary",
                        kind="counted",
                        status="active",
                        source_id=interno,
                        is_default=set_key == contest_key,
                        completeness=completeness,
                    )
                    cs.add("note", entity_id=result_set, kind="methodology", body_md=note)
                    for region, registered, voters, yes_votes, no_votes, blank, valid, invalid in rows_in.select(
                        "region", "registered", "voters", "yes", "no", "blank", "valid", "invalid"
                    ).iter_rows():
                        unit = str({"ESTERO": abroad, "IT": nation}.get(region) or units[region])
                        for option, measure, value in (
                            (options["yes"], VOTES, yes_votes),
                            (options["no"], VOTES, no_votes),
                            (None, REGISTERED, registered),
                            (None, VOTERS, voters),
                            (None, VALID, valid),
                            (None, BLANK, blank),
                            (None, INVALID, invalid),
                        ):
                            if value is not None:
                                values.append(
                                    (str(result_set), unit, None if option is None else str(option), measure, value)
                                )
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                total = eligendo.select(pl.col("registered", "voters", "yes", "no").sum()).row(0, named=True)
                if number == 1:
                    first_total = total
                yes = total["yes"] / (total["yes"] + total["no"])
                turnout = total["voters"] / total["registered"]
                if kind == "abrogative" and turnout <= 0.5:
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=options["yes"],
                        kind="lost",
                        notes=OUTCOME.format(
                            turnout=f"{100 * turnout:.2f} percent",
                            abroad=", including those abroad" if day >= ABROAD else "",
                            yes=f"{100 * yes:.2f} percent",
                        ),
                    )
                else:
                    winner = "yes" if total["yes"] > total["no"] else "no"
                    for answer, option in options.items():
                        cs.add(
                            "outcome",
                            result_set_id=result_set,
                            option_id=option,
                            kind="won" if answer == winner else "lost",
                        )
            turnout_set = cs.key(f"{SOURCE}.result_set", f"{day}:referendum:turnout")
            cs.add(
                "result_set",
                id=turnout_set,
                election_id=election,
                contest_id=None,
                stage="preliminary",
                kind="counted",
                status="active",
                source_id=interno,
                is_default=True,
                completeness=1,
            )
            cs.add("note", entity_id=turnout_set, kind="methodology", body_md=TURNOUT)
            for measure, value in ((REGISTERED, first_total["registered"]), (VOTERS, first_total["voters"])):
                values.append((str(turnout_set), str(nation), None, measure, value))
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
