import json
from datetime import date
from itertools import permutations
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "ch.bfs_votes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PX = "https://www.pxweb.bfs.admin.ch/api/v1/de/px-x-1703030000_101/px-x-1703030000_101.px"
PUBLISHER = "Federal Statistical Office"
TITLE = "Volksabstimmungen: Ergebnisse Ebene Gemeinde (px-x-1703030000_101)"
GEOGRAPHY = "Kanton (-) / Bezirk (>>) / Gemeinde (......)"
CHUNK = 6
FIELDS = {"1": (2, None), "2": (3, None), "4": (4, None), "5": (1, 0), "6": (1, 1)}
PUBLISHED_BY_BFS = date(2010, 11, 28)
MOUTIER = ("6831", date(2026, 1, 1))
UNALLOCATED = 32
LANGUAGES = {"FR": "fr", "VD": "fr", "VS": "fr", "NE": "fr", "GE": "fr", "JU": "fr", "TI": "it"}
TOLERANCE = 5000
COMPLETE = 0.9999
SOURCE_NOTE = (
    "Results use the current list of communes. Where communes merged after the vote, the Federal Statistical Office "
    "has added their results together under the merged commune."
)


def query(votes: list[str]) -> dict[str, object]:
    return {
        "query": [
            {"code": GEOGRAPHY, "selection": {"filter": "all", "values": ["*"]}},
            {"code": "Datum und Vorlage", "selection": {"filter": "item", "values": votes}},
            {"code": "Ergebnis", "selection": {"filter": "item", "values": list(FIELDS)}},
        ],
        "response": {"format": "json-stat2"},
    }


def votes() -> list[str]:
    meta = json.loads(raw.fetch(SOURCE, PX, suffix=".json").path.read_text(encoding="utf-8"))
    return sorted(next(v["values"] for v in meta["variables"] if v["code"] == "Datum und Vorlage"))


def fetch() -> None:
    codes = votes()
    for start in range(0, len(codes), CHUNK):
        raw.fetch(SOURCE, PX, suffix=".json", body=query(codes[start : start + CHUNK]))


def parse() -> None:
    codes = votes()
    frames = []
    labels: dict[str, str] = {}
    for start in range(0, len(codes), CHUNK):
        data = json.loads(raw.cached(SOURCE, PX, query(codes[start : start + CHUNK])).path.read_text(encoding="utf-8"))
        geography, questions, fields = (data["dimension"][dimension]["category"] for dimension in data["id"])
        labels.update(questions["label"])
        order = sorted(geography["index"], key=geography["index"].get)
        canton = ""
        owners = []
        for code in order:
            if len(code) == 2 and code != "CH":
                canton = code
            owners.append("CH" if code == "CH" else canton)
        cells = pl.DataFrame({"value": data["value"]}, schema={"value": pl.Int64}).with_row_index("position")
        height, width = len(questions["index"]), len(fields["index"])
        question_by_index = {i: q for q, i in questions["index"].items()}
        field_by_index = {i: f for f, i in fields["index"].items()}
        frames.append(
            cells.drop_nulls("value").select(
                code=(pl.col("position") // (height * width)).replace_strict(
                    dict(enumerate(order)), return_dtype=pl.String
                ),
                canton=(pl.col("position") // (height * width)).replace_strict(
                    dict(enumerate(owners)), return_dtype=pl.String
                ),
                question=((pl.col("position") // width) % height).replace_strict(
                    question_by_index, return_dtype=pl.String
                ),
                field=(pl.col("position") % width).replace_strict(field_by_index, return_dtype=pl.String),
                value="value",
            )
        )
    frame = pl.concat(frames).join(
        pl.DataFrame(
            {"code": list(geography["label"]), "label": [t.lstrip(".- ") for t in geography["label"].values()]}
        ),
        on="code",
    )
    communes = frame.filter(pl.col("code").str.len_chars() == 4)
    dates = {question: date.fromisoformat(label[:10]) for question, label in labels.items()}
    moutier = pl.col("code") == MOUTIER[0]
    after = pl.col("question").replace_strict({q: d >= MOUTIER[1] for q, d in dates.items()}, return_dtype=pl.Boolean)
    communes = communes.with_columns(
        canton=pl.when(moutier & ~after).then(pl.lit("BE")).when(moutier).then(pl.lit("JU")).otherwise("canton")
    )
    cantons = frame.filter(pl.col("code").str.len_chars() == 2, pl.col("code") != "CH")
    keys = ["canton", "question", "field"]
    broken = []
    for parts, totals in (
        (communes.group_by(keys).agg(pl.col("value").sum()), cantons),
        (
            cantons.with_columns(canton=pl.lit("CH")).group_by(keys).agg(pl.col("value").sum()),
            frame.filter(pl.col("code") == "CH"),
        ),
    ):
        broken.append(
            totals.select(pl.col("code").alias("canton"), "question", "field", "value")
            .join(parts, on=keys, how="full", suffix="_sum", coalesce=True)
            .filter(pl.col("value").ne_missing(pl.col("value_sum")))
        )
    mismatched = pl.concat(broken)
    print(
        f"{mismatched.height} total cells differ from the sum of their parts, in {mismatched['question'].n_unique()} votes"
    )
    print(mismatched.group_by("question").len().sort("len", descending=True).head(15))
    PARSED.mkdir(parents=True, exist_ok=True)
    communes.write_parquet(PARSED / "votes.parquet")
    national = frame.filter(pl.col("code") == "CH").pivot(on="field", index="question", values="value")
    national = national.rename({"1": "registered", "2": "cast", "4": "valid", "5": "yes", "6": "no"})
    national.with_columns(
        date=pl.col("question").replace_strict({q: d for q, d in dates.items()}, return_dtype=pl.Date),
        title=pl.col("question").replace_strict({q: t[11:] for q, t in labels.items()}, return_dtype=pl.String),
    ).write_parquet(PARSED / "questions.parquet")
    mismatched.write_parquet(PARSED / "mismatched.parquet")


def emit() -> Path:
    votes = pl.read_parquet(PARSED / "votes.parquet")
    questions = pl.read_parquet(PARSED / "questions.parquet")
    swiss = pl.read_parquet(raw.parsed("ch.swissvotes") / "contests.parquet").filter(
        pl.col("volkja").is_not_null(), pl.col("date") != PUBLISHED_BY_BFS
    )
    pairs: dict[str, str] = {}
    for day in sorted(set(questions["date"]) - {PUBLISHED_BY_BFS}):
        ours = questions.filter(pl.col("date") == day).sort("question").to_dicts()
        theirs = swiss.filter(pl.col("date") == day).sort("anr").to_dicts()
        if not theirs:
            continue
        size = min(len(ours), len(theirs))
        best = min(
            (
                (
                    sum(
                        abs(a["yes"] - b["volkja"]) + abs(a["no"] - b["volknein"])
                        for a, b in zip(ours, pick, strict=False)
                    ),
                    pick,
                )
                for pick in permutations(theirs, size)
            ),
            key=lambda found: found[0],
        )
        for a, b in zip(ours, best[1], strict=False):
            if abs(a["yes"] - b["volkja"]) + abs(a["no"] - b["volknein"]) <= TOLERANCE:
                pairs[a["question"]] = b["anr"]
    print(f"{len(pairs)} of {questions.height} votes matched to Swissvotes")
    kept = pl.DataFrame({"question": list(pairs), "anr": list(pairs.values())})
    votes = votes.join(kept, on="question")
    covered = {
        question: counted / (yes + no)
        for question, counted, yes, no in votes.filter(pl.col("field").is_in(["5", "6"]))
        .group_by("question")
        .agg(pl.col("value").sum())
        .join(questions.select("question", "yes", "no"), on="question")
        .select("question", "value", "yes", "no")
        .iter_rows()
    }
    shared_valid = questions.filter(pl.col("valid") != pl.col("yes") + pl.col("no"))["question"]
    votes = votes.filter(~((pl.col("field") == "4") & pl.col("question").is_in(shared_valid)))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Swiss federal popular votes since 1960 by commune",
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
        for start in range(0, len(votes_codes := sorted(questions["question"])), CHUNK):
            cs.artifact(raw.cached(SOURCE, PX, query(votes_codes[start : start + CHUNK])), source)

        switzerland = cs.polity("CH")
        cantons = {code: cs.area("ch.canton", code) for code in votes["canton"].unique()}
        if cs.minted & {switzerland, *cantons.values()}:
            raise ValueError("run ch.swisstopo first: Switzerland and its cantons must exist")
        units: dict[str, UUID] = {}
        channels: dict[str, tuple[int, int]] = {}
        for code, label, canton in votes.select("code", "label", "canton").unique("code").sort("code").iter_rows():
            units[code] = cs.unit("ch.commune", code)
            channels[code] = (0, 0)
            if not code.startswith("9"):
                if units[code] in cs.minted:
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
            if units[code] in cs.minted:
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

        sets: dict[str, tuple[UUID, UUID]] = {}
        for question, anr in pairs.items():
            contest = cs.key("ch.contest", f"swissvotes:{anr}")
            old = cs.key("ch.swissvotes.result_set", f"swissvotes:{anr}")
            if contest in cs.minted or old in cs.minted:
                raise ValueError(f"run ch.swissvotes first: vote {anr} is missing")
            fields = ("election_id", "stage", "kind", "status", "source_id", "as_of", "completeness", "supersedes_id")
            held = conn.execute(
                f"select contest_id, {', '.join(fields)} from result_set where id = %s", [old]
            ).fetchone()
            if held is None:
                raise ValueError(f"vote {anr}: no Swissvotes result set")
            replacement = cs.key(f"{SOURCE}.result_set", f"swissvotes:{anr}")
            sets[question] = (replacement, old)
            full = covered[question] >= COMPLETE
            if full:
                cs.add(
                    "result_set",
                    id=old,
                    contest_id=held[0],
                    **dict(zip(fields, held[1:], strict=True)),
                    is_default=False,
                )
            cs.add(
                "result_set",
                id=replacement,
                election_id=held[1],
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=full,
                completeness=min(covered[question], 1),
                supersedes_id=old if full else None,
            )
            for option in conn.execute(
                "select option_id, kind from outcome where result_set_id = %s", [old]
            ).fetchall():
                cs.add("outcome", result_set_id=replacement, option_id=option[0], kind=option[1])
        minted = [question for question, (replacement, _) in sets.items() if replacement in cs.minted]
        option_ids = {
            (question, field): None
            if position is None
            else str(cs.key("ch.option", f"swissvotes:{pairs[question]}:{position}"))
            for question in minted
            for field, (_, position) in FIELDS.items()
        }
        cs.frame(
            "result_value",
            votes.filter(pl.col("question").is_in(minted)).select(
                result_set_id=pl.col("question").replace_strict({q: str(s[0]) for q, s in sets.items()}),
                unit_id=pl.col("code").replace_strict({code: str(unit) for code, unit in units.items()}),
                option_id=pl.concat_str("question", pl.lit(":"), "field").replace_strict(
                    {f"{q}:{f}": v for (q, f), v in option_ids.items()}, return_dtype=pl.String
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
