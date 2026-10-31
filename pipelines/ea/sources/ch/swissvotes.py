from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.store import DATA, connect

SOURCE = "ch.swissvotes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://swissvotes.ch/page/dataset/swissvotes_dataset.csv"
PUBLISHER = "Année Politique Suisse, University of Bern"
TITLE = "Swissvotes: the database on Swiss popular votes (DOI 10.48620/365)"
PUBLISHED_BY_BFS = date(2010, 11, 28)
CANTONS = [
    "ZH",
    "BE",
    "LU",
    "UR",
    "SZ",
    "OW",
    "NW",
    "GL",
    "ZG",
    "FR",
    "SO",
    "BS",
    "BL",
    "SH",
    "AR",
    "AI",
    "SG",
    "GR",
    "AG",
    "TG",
    "TI",
    "VD",
    "VS",
    "NE",
    "GE",
    "JU",
]
HALF_CANTONS = ("OW", "NW", "BS", "BL", "AR", "AI")
TYPES = {
    "1": "mandatory_referendum",
    "2": "optional_referendum",
    "3": "initiative",
    "4": "counter_proposal",
    "5": "tie_break",
}
MONTHS = {
    "de": [
        "Januar",
        "Februar",
        "März",
        "April",
        "Mai",
        "Juni",
        "Juli",
        "August",
        "September",
        "Oktober",
        "November",
        "Dezember",
    ],
    "fr": [
        "janvier",
        "février",
        "mars",
        "avril",
        "mai",
        "juin",
        "juillet",
        "août",
        "septembre",
        "octobre",
        "novembre",
        "décembre",
    ],
    "en": [
        "January",
        "February",
        "March",
        "April",
        "May",
        "June",
        "July",
        "August",
        "September",
        "October",
        "November",
        "December",
    ],
}
FIELDS = {"berecht": (2, None), "stimmen": (3, None), "gultig": (4, None), "ja": (1, 0), "nein": (1, 1)}
SOURCE_NOTE = (
    "Swissvotes compiles the results of every federal vote from the Federal Chancellery (to 1980) and the Federal "
    "Statistical Office (from 1981). Canton counts are the cantons' own, so they can differ slightly from the national "
    "totals the Chancellery declares."
)
VALID_NOTE = (
    "Valid ballots are not shown for this question. Swissvotes counts them across the initiative and its "
    "counter-proposal together, so they include ballots left blank on this question."
)
MOOT = "The counter-proposal was rejected, so this tie-break question had no effect."


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".csv")


def parse() -> None:
    frame = pl.read_csv(
        raw.cached(SOURCE, URL).path, separator=";", encoding="utf8-lossy", infer_schema_length=0
    ).filter(pl.col("volk").is_not_null())
    national = ["berecht", "stimmen", "gultig", "volkja", "volknein"]
    contests = frame.select(
        "anr",
        pl.col("datum").str.to_date("%d.%m.%Y").alias("date"),
        "rechtsform",
        "titel_kurz_d",
        "titel_kurz_f",
        "titel_kurz_e",
        "titel_off_d",
        "titel_off_f",
        "annahme",
        "swissvoteslink",
        *[pl.col(column).replace(".", None).cast(pl.Int64) for column in national],
    )
    rows = []
    for row in frame.iter_rows(named=True):
        for canton in CANTONS:
            values = {field: row[f"{canton.lower()}-{field}"] for field in FIELDS}
            counts = {field: None if text in (None, ".", "") else int(float(text)) for field, text in values.items()}
            if counts["ja"] is None or counts["nein"] is None:
                continue
            if counts["stimmen"] is not None and counts["gultig"] is not None and counts["stimmen"] < counts["gultig"]:
                raise ValueError(f"{row['anr']} {canton}: ballots cast below valid votes")
            rows.append({"anr": row["anr"], "canton": canton, **counts})
    cantons = pl.DataFrame(rows, schema={"anr": pl.String, "canton": pl.String, **dict.fromkeys(FIELDS, pl.Int64)})
    PARSED.mkdir(parents=True, exist_ok=True)
    contests.write_parquet(PARSED / "contests.parquet")
    cantons.write_parquet(PARSED / "cantons.parquet")
    print(f"{contests.height} votes, {cantons.height} canton rows")


def emit() -> Path:
    cantons = pl.read_parquet(PARSED / "cantons.parquet")
    contests = pl.read_parquet(PARSED / "contests.parquet").filter(
        pl.col("date") != PUBLISHED_BY_BFS, pl.col("anr").is_in(cantons["anr"]) | pl.col("volkja").is_not_null()
    )
    by_vote = {anr: frame for (anr,), frame in cantons.group_by("anr")}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Swiss federal popular votes from 1848, national and by canton (Swissvotes)",
            polity="CH",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://swissvotes.ch/page/dataset/swissvotes_dataset.csv",
            license="CC BY 4.0",
            license_url="https://creativecommons.org/licenses/by/4.0/",
            notes=SOURCE_NOTE,
        )
        artifact = raw.cached(SOURCE, URL)
        cs.artifact(artifact, source)

        switzerland = cs.polity("CH")
        areas = {code: cs.area("ch.canton", code) for code in CANTONS}
        nation = cs.area("ch.nation", "CH")
        if cs.minted & {switzerland, nation, *areas.values()}:
            raise ValueError("run ch.swisstopo first: Switzerland and its cantons must exist")
        units: dict[str, UUID] = {}
        for scheme, code, area in [
            ("ch.nation", "CH", nation),
            *[("ch.canton", code, areas[code]) for code in CANTONS],
        ]:
            units[code] = cs.unit(scheme, code)
            if units[code] in cs.minted:
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=switzerland,
                    scheme_code=scheme,
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )

        system = cs.key("ch.electoral_system", "federal-referendum")
        if system in cs.minted:
            raise ValueError("run ch.bfs first: the federal referendum system must exist")

        elections: dict[date, UUID] = {}
        parts: dict[date, set[str]] = {}
        for contest_row in contests.sort("date", "anr").iter_rows(named=True):
            day: date = contest_row["date"]
            anr: str = contest_row["anr"]
            kind = TYPES[contest_row["rechtsform"]]
            if day not in elections:
                elections[day] = cs.key("ch.election", str(day))
                if elections[day] in cs.minted:
                    count = contests.filter(pl.col("date") == day).height
                    cs.add(
                        "election",
                        id=elections[day],
                        polity_id=switzerland,
                        kind="referendum",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    label = "referendums" if count > 1 else "referendum"
                    name(
                        elections[day], f"Swiss federal {label} of {MONTHS['en'][day.month - 1]} {day.day}, {day.year}"
                    )
                    name(
                        elections[day],
                        f"Eidgenössische Volksabstimmung vom {day.day}. {MONTHS['de'][day.month - 1]} {day.year}",
                        "de",
                        "native",
                    )
                    first = "1er" if day.day == 1 else str(day.day)
                    name(
                        elections[day],
                        f"Votation populaire fédérale du {first} {MONTHS['fr'][day.month - 1]} {day.year}",
                        "fr",
                        "native",
                    )
                    cs.add("slug", path=f"ch/{day}-referendum", entity_id=elections[day])
                parts[day] = set()
            election = elections[day]
            contest_key = f"swissvotes:{anr}"
            contest = cs.key("ch.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="ballot_question",
                electoral_system_id=system,
                round_number=1,
                default_result_kind="counted",
            )
            cs.add("ballot_question", contest_id=contest, type=kind)
            short = contest_row["titel_kurz_e"]
            if short is not None:
                name(contest, short)
                name(contest, contest_row["titel_kurz_d"], "de", "native")
            else:
                name(contest, contest_row["titel_kurz_d"], "de")
            name(contest, contest_row["titel_kurz_f"], "fr", "native")
            part = slug(short or contest_row["titel_kurz_d"])
            if len(part) > 60:
                part = part[:61].rsplit("-", 1)[0]
            part = part or f"vote-{anr}"
            if part in parts[day]:
                part = f"{part}-{slug(anr)}"
            parts[day].add(part)
            cs.add("slug", path=f"ch/{day}-referendum/{part}", entity_id=contest)
            for lang, column in (("de", "titel_off_d"), ("fr", "titel_off_f")):
                cs.add(
                    "ballot_question_text",
                    id=uuid5(NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{lang}"),
                    contest_id=contest,
                    kind="text",
                    lang=lang,
                    body=contest_row[column],
                    url=contest_row["swissvoteslink"],
                    source_id=source,
                    locator={"anr": anr},
                )
            labels = ("Initiative", "Counter-proposal") if kind == "tie_break" else ("Yes", "No")
            options: dict[int, UUID] = {}
            for position, label in enumerate(labels):
                options[position] = cs.key("ch.option", f"{contest_key}:{position}")
                cs.add(
                    "contest_option",
                    id=options[position],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
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
            adopted = contest_row["annahme"]
            winner = {"1": 0, "0": 1, "9": 0, "8": 1}.get(adopted)
            if winner is not None:
                cs.add("outcome", result_set_id=result_set, option_id=options[winner], kind="won")
            elif kind == "tie_break":
                cs.add("note", entity_id=contest, kind="context", body_md=MOOT)
            votes = by_vote.get(anr)
            if votes is None:
                votes = pl.DataFrame(
                    [
                        {
                            "anr": anr,
                            "canton": "CH",
                            "berecht": contest_row["berecht"],
                            "stimmen": contest_row["stimmen"],
                            "gultig": contest_row["gultig"],
                            "ja": contest_row["volkja"],
                            "nein": contest_row["volknein"],
                        }
                    ],
                    schema=cantons.schema,
                )
            else:
                mismatched = votes.filter(pl.col("gultig") != pl.col("ja") + pl.col("nein"))
                if mismatched.height:
                    votes = votes.with_columns(
                        gultig=pl.when(pl.col("gultig") == pl.col("ja") + pl.col("nein")).then(pl.col("gultig"))
                    )
                    cs.add("note", entity_id=contest, kind="context", body_md=VALID_NOTE)
                yes, no = votes["ja"].sum(), votes["nein"].sum()
                if (yes, no) != (contest_row["volkja"], contest_row["volknein"]):
                    cs.add(
                        "note",
                        entity_id=contest,
                        kind="context",
                        body_md=(
                            f"The cantons' counts add up to {yes:,} Yes and {no:,} No. The national totals in the "
                            f"Swissvotes dataset are {contest_row['volkja']:,} Yes and {contest_row['volknein']:,} No."
                            if contest_row["volkja"] is not None
                            else f"The cantons' counts add up to {yes:,} Yes and {no:,} No; the dataset gives no national totals."
                        ),
                    )
            if result_set in cs.minted:
                cs.frame(
                    "result_value",
                    votes.unpivot(on=list(FIELDS), index="canton", variable_name="field", value_name="value")
                    .drop_nulls("value")
                    .select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id=pl.col("canton").replace_strict({code: str(unit) for code, unit in units.items()}),
                        option_id=pl.col("field").replace_strict(
                            {
                                field: None if position is None else str(options[position])
                                for field, (_, position) in FIELDS.items()
                            },
                            return_dtype=pl.String,
                        ),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.col("field").replace_strict(
                            {field: measure for field, (measure, _) in FIELDS.items()}, return_dtype=pl.Int16
                        ),
                        count_round=pl.lit(0, pl.Int16),
                        flags=pl.lit(0, pl.Int16),
                        value="value",
                    ),
                )
        print(f"{contests.height} votes on {len(elections)} days")
        return cs.write()
