import json
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "ch.bfs"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PX = "https://www.pxweb.bfs.admin.ch/api/v1/de/px-x-1703030000_101/px-x-1703030000_101.px"
PUBLISHER = "Federal Statistical Office"
TITLE = "Volksabstimmungen: Ergebnisse Ebene Gemeinde (px-x-1703030000_101)"
VOTE = date(2010, 11, 28)
QUESTIONS = {
    "5521": (
        "initiative",
        "Popular initiative “for the deportation of criminal foreigners”",
        "deportation-initiative",
        "yes",
    ),
    "5522": (
        "counter_proposal",
        "Federal decree on the deportation of criminal foreigners (counter-proposal)",
        "deportation-counter-proposal",
        "no",
    ),
    "5523": (
        "tie_break",
        "Deportation initiative and counter-proposal: tie-break question",
        "deportation-tie-break",
        None,
    ),
    "5530": ("initiative", "Popular initiative “for fair taxes”", "fair-taxes-initiative", "no"),
}
FIELDS = {"1": (2, None), "2": (3, None), "4": (4, None), "5": (1, 0), "6": (1, 1)}
QUERY: dict[str, object] = {
    "query": [
        {"code": "Kanton (-) / Bezirk (>>) / Gemeinde (......)", "selection": {"filter": "all", "values": ["*"]}},
        {"code": "Datum und Vorlage", "selection": {"filter": "item", "values": list(QUESTIONS)}},
        {"code": "Ergebnis", "selection": {"filter": "item", "values": list(FIELDS)}},
    ],
    "response": {"format": "json-stat2"},
}
HALF_CANTONS = ("OW", "NW", "BS", "BL", "AR", "AI")
LANGUAGES = {"FR": "fr", "VD": "fr", "VS": "fr", "NE": "fr", "GE": "fr", "JU": "fr", "TI": "it"}
UNALLOCATED = 32
MOUTIER = ("6831", "BE")
SOURCE_NOTE = (
    "Results use the current list of communes. Where communes merged after the vote, the Federal Statistical Office "
    "has added their results together under the merged commune."
)
MOOT = "The counter-proposal was rejected, so this tie-break question had no effect."


def fetch() -> None:
    raw.fetch(SOURCE, PX, suffix=".json", body=QUERY)


def parse() -> None:
    data = json.loads(raw.cached(SOURCE, PX, QUERY).path.read_text(encoding="utf-8"))
    geography, questions, fields = (data["dimension"][dimension]["category"] for dimension in data["id"])
    _, height, width = data["size"]
    rows: list[tuple[str, str, str, str, str, int]] = []
    canton = ""
    for code in sorted(geography["index"], key=geography["index"].get):
        if len(code) == 2 and code != "CH":
            canton = code
        if len(code) == 6:
            continue
        for question in QUESTIONS:
            for field in FIELDS:
                position = (geography["index"][code] * height + questions["index"][question]) * width
                value = data["value"][position + fields["index"][field]]
                if value is not None:
                    owner = MOUTIER[1] if code == MOUTIER[0] else "CH" if code == "CH" else canton
                    rows.append((code, geography["label"][code].lstrip(".- "), owner, question, field, value))
    frame = pl.DataFrame(
        rows,
        schema={
            "code": pl.String,
            "label": pl.String,
            "canton": pl.String,
            "question": pl.String,
            "field": pl.String,
            "value": pl.Int64,
        },
        orient="row",
    )
    communes = frame.filter(pl.col("code").str.len_chars() == 4)
    cantons = frame.filter(pl.col("code").str.len_chars() == 2, pl.col("code") != "CH")
    keys = ["canton", "question", "field"]
    for parts, totals in (
        (communes.group_by(keys).agg(pl.col("value").sum()), cantons),
        (
            cantons.with_columns(canton=pl.lit("CH")).group_by(keys).agg(pl.col("value").sum()),
            frame.filter(pl.col("code") == "CH"),
        ),
    ):
        mismatched = (
            totals.select(pl.col("code").alias("canton"), "question", "field", "value")
            .join(parts, on=keys, how="full", suffix="_sum", coalesce=True)
            .filter(pl.col("value").ne_missing(pl.col("value_sum")))
        )
        if mismatched.height:
            raise ValueError(f"totals differ from the sum of their parts: {mismatched.head(5)}")
    PARSED.mkdir(parents=True, exist_ok=True)
    communes.write_parquet(PARSED / "votes.parquet")
    pl.DataFrame(
        [(question, questions["label"][question].removeprefix(f"{VOTE} ")) for question in QUESTIONS],
        schema={"question": pl.String, "title": pl.String},
        orient="row",
    ).write_parquet(PARSED / "questions.parquet")


def emit() -> Path:
    votes = pl.read_parquet(PARSED / "votes.parquet")
    titles = dict(pl.read_parquet(PARSED / "questions.parquet").iter_rows())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Swiss federal referendums of November 28, 2010, by commune",
            polity="CH",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.pxweb.bfs.admin.ch/pxweb/de/px-x-1703030000_101/px-x-1703030000_101.px/",
            license="Open use; must provide the source",
            license_url="https://www.bfs.admin.ch/bfs/en/home/fso/swiss-federal-statistical-office/terms-of-use.html",
            notes=SOURCE_NOTE,
        )
        cs.artifact(raw.cached(SOURCE, PX, QUERY), source)

        switzerland = cs.polity("CH")
        cantons = {code: cs.area("ch.canton", code) for code in votes["canton"].unique()}
        if cs.minted & {switzerland, *cantons.values()}:
            raise ValueError("run ch.swisstopo first: Switzerland and its cantons must exist")
        units: dict[str, UUID] = {}
        channels: dict[str, tuple[int, int]] = {}
        for code, label, canton in votes.select("code", "label", "canton").unique().sort("code").iter_rows():
            units[code] = cs.unit("ch.commune", code)
            channels[code] = (0, 0)
            if not code.startswith("9"):
                area = cs.area("ch.commune", code)
                if area in cs.minted:
                    raise ValueError(f"commune {code} ({label}) has no area")
                cs.add(
                    "reporting_unit",
                    id=units[code],
                    polity_id=switzerland,
                    scheme_code="ch.commune",
                    code=code,
                    area_relation="equals",
                    area_id=area,
                    kind="standard",
                    valid_from=None,
                )
                continue
            postal = "Korrespondenz" in label or "corrispondenza" in label
            abroad = any(word in label for word in ("Ausland", "étranger", "estero"))
            channels[code] = (3 if postal else 4 if abroad else 0, UNALLOCATED)
            cs.add(
                "reporting_unit",
                id=units[code],
                polity_id=switzerland,
                scheme_code="ch.commune",
                code=code,
                area_relation="within",
                area_id=cantons[canton],
                kind="postal_district" if postal else "aggregate",
                valid_from=None,
            )
            name(units[code], label, LANGUAGES.get(canton, "de"))

        system = cs.key("ch.electoral_system", "federal-referendum")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=switzerland,
            rules={
                "formula": "referendum",
                "doubleMajority": {
                    "scheme": "ch.canton",
                    "weights": dict.fromkeys(HALF_CANTONS, 0.5),
                    "types": ["initiative", "mandatory_referendum", "counter_proposal"],
                },
            },
            rules_version=1,
        )
        name(system, "Swiss federal referendum")
        election = cs.key("ch.election", str(VOTE))
        cs.add(
            "election",
            id=election,
            polity_id=switzerland,
            kind="referendum",
            date_from=VOTE,
            date_to=VOTE,
            status="held",
        )
        name(election, "Swiss federal referendums of November 28, 2010")
        name(election, "Eidgenössische Volksabstimmung vom 28. November 2010", "de", "native")
        cs.add("slug", path=f"ch/{VOTE}-referendum", entity_id=election)

        result_sets: dict[str, UUID] = {}
        options: dict[tuple[str, int], UUID] = {}
        for question, (kind, title, part, result) in QUESTIONS.items():
            contest_key = f"{VOTE}:{question}"
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
            name(contest, title)
            name(contest, titles[question], "de", "native")
            cs.add("slug", path=f"ch/{VOTE}-referendum/{part}", entity_id=contest)
            labels = ("Initiative", "Counter-proposal") if kind == "tie_break" else ("Yes", "No")
            for position, label in enumerate(labels):
                options[(question, position)] = cs.key("ch.option", f"{contest_key}:{position}")
                cs.add(
                    "contest_option",
                    id=options[(question, position)],
                    contest_id=contest,
                    kind="answer",
                    ballot_position=position + 1,
                    ballot_label=label,
                )
            result_sets[question] = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_sets[question],
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            if result is None:
                cs.add("note", entity_id=contest, kind="context", body_md=MOOT)
            else:
                cs.add(
                    "outcome",
                    result_set_id=result_sets[question],
                    option_id=options[(question, 0 if result == "yes" else 1)],
                    kind="won",
                )

        minted = [question for question, result_set in result_sets.items() if result_set in cs.minted]
        cs.frame(
            "result_value",
            votes.filter(pl.col("question").is_in(minted)).select(
                result_set_id=pl.col("question").replace_strict({q: str(id) for q, id in result_sets.items()}),
                unit_id=pl.col("code").replace_strict({code: str(id) for code, id in units.items()}),
                option_id=pl.concat_str("question", pl.lit(":"), "field").replace_strict(
                    {
                        f"{question}:{field}": None if position is None else str(options[(question, position)])
                        for question in QUESTIONS
                        for field, (_, position) in FIELDS.items()
                    },
                    return_dtype=pl.String,
                ),
                channel=pl.col("code").replace_strict(
                    {code: channel for code, (channel, _) in channels.items()}, return_dtype=pl.Int16
                ),
                measure=pl.col("field").replace_strict(
                    {field: measure for field, (measure, _) in FIELDS.items()}, return_dtype=pl.Int16
                ),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.col("code").replace_strict(
                    {code: flags for code, (_, flags) in channels.items()}, return_dtype=pl.Int16
                ),
                value="value",
            ),
        )
        return cs.write()
