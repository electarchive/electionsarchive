import html
import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID, uuid5

import polars as pl

from ea import raw
from ea.changeset import NAMESPACE, Changeset, slug
from ea.sources.ch.swissvotes import CANTONS, FIELDS, MONTHS, MOOT, TYPES
from ea.store import DATA, connect

SOURCE = "ch.voteinfo"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://ogd-static.voteinfo-app.ch/v1/ogd/sd-t-17-02-{day}-eidgAbstimmung.json"
PUBLISHER = "Swiss Federal Chancellery"
TITLE = "Eidgenössische Abstimmungen: Resultate (Open Government Data, voteinfo)"
UPCOMING = date(2026, 11, 29)
SWISSVOTES = "https://swissvotes.ch/page/dataset/swissvotes_dataset.csv"
DAYS = [
    date(2024, 6, 9),
    date(2024, 9, 22),
    date(2024, 11, 24),
    date(2025, 2, 9),
    date(2025, 9, 28),
    date(2025, 11, 30),
    date(2026, 3, 8),
    date(2026, 6, 14),
    date(2026, 9, 27),
]
LANGUAGES = ("de", "fr", "it", "rm", "en")
FEDLEX = "https://www.fedlex.admin.ch/eli/fga/2026/{number}/{lang}"
CIRCULAR = (
    "https://www.fedlex.admin.ch/filestore/fedlex.data.admin.ch/eli/fga/2026/2406/{lang}/html/"
    "fedlex-data-admin-ch-eli-fga-2026-2406-{lang}-html.html"
)
QUESTIONS = (
    (
        "AHV",
        1757,
        "Decreto federale sul finanziamento supplementare dell’AVS mediante un aumento dell’imposta sul valore aggiunto",
    ),
    ("Feuerwerk", 1760, "Iniziativa popolare «Per una limitazione dei fuochi d’artificio»"),
    (
        "Ehepaare",
        1758,
        "Iniziativa popolare «Sì a imposte federali eque anche per i coniugi – Basta con la discriminazione del matrimonio!»",
    ),
    ("Kriegsmaterial", 22, "Modifica della legge federale sul materiale bellico (LMB)"),
)
ASKED = ("de", "fr", "it")
UNALLOCATED = 32
VALID_NOTE = (
    "The Federal Chancellery counts valid ballots across the initiative and its counter-proposal together, so they "
    "include ballots left blank on this question. They are not shown here."
)
PROVISIONAL_NOTE = (
    "These are the cantons' provisional results as the Federal Chancellery published them. The Federal Council "
    "validates them after the appeal period ends."
)
SOURCE_NOTE = "Results use the commune boundaries in force on the day of the vote."
MOUTIER = ("6831", date(2026, 1, 1))
KINDS = {"1": "1", "2": "2", "3": "3", "4": "3", "5": "4", "6": "5"}


def url(day: date) -> str:
    return URL.format(day=f"{day:%Y%m%d}")


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".json")
    raw.fetch(SOURCE, SWISSVOTES, suffix=".csv")
    for lang in ASKED:
        raw.fetch(SOURCE, CIRCULAR.format(lang=lang), suffix=".html")


def parse() -> None:
    contests = []
    results = []
    for day in DAYS:
        data = json.loads(raw.cached(SOURCE, url(day)).path.read_text(encoding="utf-8"))
        if date.fromisoformat(f"{data['abstimmtag'][:4]}-{data['abstimmtag'][4:6]}-{data['abstimmtag'][6:]}") != day:
            raise ValueError(f"{day}: the file is for {data['abstimmtag']}")
        for vote in data["schweiz"]["vorlagen"]:
            titles = {title["langKey"]: title["text"] for title in vote["vorlagenTitel"]}
            national = vote["resultat"]
            contests.append(
                {
                    "date": day,
                    "vote": vote["vorlagenId"],
                    "kind": str(vote["vorlagenArtId"]),
                    "main": vote["hauptvorlagenId"],
                    "finished": vote["vorlageBeendet"],
                    "provisional": vote["provisorisch"],
                    "accepted": vote["vorlageAngenommen"],
                    "double": vote["doppeltesMehr"],
                    "scheduled": False,
                    "cantons_yes": vote["staende"]["jaStaendeGanz"] + vote["staende"]["jaStaendeHalb"] / 2,
                    "cantons_no": vote["staende"]["neinStaendeGanz"] + vote["staende"]["neinStaendeHalb"] / 2,
                    **{f"title_{lang}": titles.get(lang) for lang in LANGUAGES},
                }
            )
            levels = [("nation", "CH", "CH", None, national)]
            for canton in vote["kantone"]:
                abbreviation = CANTONS[int(canton["geoLevelnummer"]) - 1]
                levels.append(("canton", abbreviation, abbreviation, None, canton["resultat"]))
                levels.extend(
                    (
                        "commune",
                        f"{int(town['geoLevelnummer']):04d}",
                        abbreviation,
                        town["geoLevelname"],
                        town["resultat"],
                    )
                    for town in canton["gemeinden"]
                )
            for level, code, canton, label, result in levels:
                results.append(
                    {
                        "date": day,
                        "vote": vote["vorlagenId"],
                        "level": level,
                        "code": code,
                        "canton": canton,
                        "label": label,
                        "counted": result["gebietAusgezaehlt"],
                        "registered": result["anzahlStimmberechtigte"],
                        "cast": result["eingelegteStimmzettel"],
                        "valid": result["gueltigeStimmen"],
                        "yes": result["jaStimmenAbsolut"],
                        "no": result["neinStimmenAbsolut"],
                    }
                )
    upcoming = pl.read_csv(
        raw.cached(SOURCE, SWISSVOTES).path, separator=";", encoding="utf8-lossy", infer_schema_length=0
    ).filter(pl.col("datum").str.to_date("%d.%m.%Y") == UPCOMING)
    asked = {}
    for lang in ASKED:
        text = re.sub(
            r"\s+",
            " ",
            html.unescape(
                re.sub(r"<[^>]+>", "", raw.cached(SOURCE, CIRCULAR.format(lang=lang)).path.read_text(encoding="utf-8"))
            ),
        )
        asked[lang] = re.findall(r"(?:Wollen Sie|Acceptez-vous|Volete accettare)[^?]+\?", text)
        if len(asked[lang]) != len(QUESTIONS):
            raise ValueError(f"{lang}: {len(asked[lang])} ballot questions in the circular")
    for row in upcoming.iter_rows(named=True):
        vote = round(float(row["anr"]) * 10)
        order = next(i for i, (keyword, _, _) in enumerate(QUESTIONS) if keyword in row["titel_off_d"])
        contests.append(
            {
                "date": UPCOMING,
                "vote": vote,
                "kind": {"1": "1", "2": "2", "3": "3", "4": "5", "5": "6"}[row["rechtsform"]],
                "main": None,
                "finished": False,
                "provisional": False,
                "accepted": False,
                "double": False,
                "cantons_yes": 0.0,
                "cantons_no": 0.0,
                "title_de": row["titel_off_d"],
                "title_fr": row["titel_off_f"],
                "title_it": QUESTIONS[order][2],
                "title_rm": None,
                "title_en": row["titel_kurz_e"],
                "scheduled": True,
                "gazette": QUESTIONS[order][1],
                **{f"question_{lang}": asked[lang][order] for lang in ASKED},
            }
        )
    frame = pl.DataFrame(results, infer_schema_length=None)
    parts = frame.filter(pl.col("level") == "commune").with_columns(
        canton=pl.when((pl.col("code") == MOUTIER[0]) & (pl.col("date") < MOUTIER[1]))
        .then(pl.lit("BE"))
        .otherwise("canton")
    )
    keys = ["date", "vote", "canton"]
    totals = ["registered", "cast", "valid", "yes", "no"]
    broken = (
        parts.group_by(keys)
        .agg(pl.col(totals).sum())
        .join(frame.filter(pl.col("level") == "canton").drop("level", "code", "label", "counted"), on=keys, suffix="_c")
        .filter(pl.any_horizontal(pl.col(field) != pl.col(f"{field}_c") for field in totals))
    )
    if broken.height:
        raise ValueError(f"{broken.height} cantons differ from the sum of their communes:\n{broken}")
    nation = frame.filter(pl.col("level") == "nation").select("date", "vote", *totals)
    summed = frame.filter(pl.col("level") == "canton").group_by("date", "vote").agg(pl.col(totals).sum())
    if (
        nation.join(summed, on=["date", "vote"], suffix="_c")
        .filter(pl.any_horizontal(pl.col(field) != pl.col(f"{field}_c") for field in totals))
        .height
    ):
        raise ValueError("a national total differs from the sum of its cantons")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(contests, infer_schema_length=None).write_parquet(PARSED / "contests.parquet")
    frame.write_parquet(PARSED / "results.parquet")
    print(f"{len(contests)} votes, {len(results)} result rows")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    results = pl.read_parquet(PARSED / "results.parquet")
    communes = results.filter(pl.col("level") == "commune")
    shared_valid = {
        row["vote"]
        for row in results.filter(
            pl.col("level") == "nation", pl.col("valid") != pl.col("yes") + pl.col("no")
        ).iter_rows(named=True)
    }
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Swiss federal popular votes from June 2024 by commune (Federal Chancellery)",
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
            url="https://opendata.swiss/en/dataset/echtzeitdaten-am-abstimmungstag-zu-eidgenoessischen-abstimmungsvorlagen",
            license="Open use; must provide the source",
            license_url="https://opendata.swiss/en/terms-of-use",
            notes=SOURCE_NOTE,
        )
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, url(day)), source)
        gazette = cs.source(PUBLISHER, "Bundesblatt")
        cs.add(
            "source",
            id=gazette,
            kind="official_body",
            publisher=PUBLISHER,
            title="Bundesblatt",
            url="https://www.fedlex.admin.ch/de/fga",
            license="Official acts, not protected by copyright (Art. 5 URG)",
            license_url="https://www.fedlex.admin.ch/eli/cc/1993/1798_1798_1798/de",
        )
        for lang in ASKED:
            cs.artifact(raw.cached(SOURCE, CIRCULAR.format(lang=lang)), gazette)

        switzerland = cs.polity("CH")
        system = cs.key("ch.electoral_system", "federal-referendum")
        if system in cs.minted:
            raise ValueError("run ch.bfs first: the federal referendum system must exist")
        units: dict[str, UUID] = {}
        channels: dict[str, tuple[int, int]] = {}
        for code, label in communes.select("code", "label").unique("code").sort("code").iter_rows():
            units[code] = cs.unit("ch.commune", code)
            if units[code] in cs.minted:
                raise ValueError(f"commune {code} ({label}) has no unit: run ch.bfs_votes first")
            channels[code] = (4, UNALLOCATED) if code >= "9000" else (0, 0)

        elections: dict[date, UUID] = {}
        parts: dict[date, set[str]] = {}
        for row in contests.sort("date", "vote").iter_rows(named=True):
            day: date = row["date"]
            vote: int = row["vote"]
            kind = TYPES[KINDS[row["kind"]]]
            if day not in elections:
                elections[day] = cs.key("ch.election", str(day))
                if elections[day] in cs.minted:
                    count = contests.filter(pl.col("date") == day, pl.col("kind") != "6").height
                    cs.add(
                        "election",
                        id=elections[day],
                        polity_id=switzerland,
                        kind="referendum",
                        date_from=day,
                        date_to=day,
                        status="scheduled" if day == UPCOMING else "held",
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
            contest_key = f"voteinfo:{vote}"
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
            for lang in LANGUAGES:
                if row[f"title_{lang}"] is not None:
                    name(contest, row[f"title_{lang}"], lang, "official" if lang == "en" else "native")
            part = slug(row["title_en"] or row["title_de"])[:60].strip("-") or f"vote-{vote}"
            if part in parts[day]:
                part = f"{part}-{vote}"
            parts[day].add(part)
            cs.add("slug", path=f"ch/{day}-referendum/{part}", entity_id=contest)
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
            if row["scheduled"]:
                for lang in ASKED:
                    circular = FEDLEX.format(number=2406, lang=lang)
                    official = FEDLEX.format(number=row["gazette"], lang=lang)
                    for text_kind, body, address in (
                        ("text", row[f"question_{lang}"], circular),
                        ("link", None, official),
                    ):
                        cs.add(
                            "ballot_question_text",
                            id=uuid5(
                                NAMESPACE, f"ballot_question_text\x1f{contest}\x1f{text_kind}\x1f{lang}\x1f{gazette}"
                            ),
                            contest_id=contest,
                            kind=text_kind,
                            lang=lang,
                            body=body,
                            url=address,
                            source_id=gazette,
                        )
                continue
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary" if row["provisional"] else "official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=True,
                completeness=1,
            )
            if kind == "tie_break":
                cs.add("note", entity_id=contest, kind="context", body_md=MOOT)
            else:
                cs.add("outcome", result_set_id=result_set, option_id=options[0 if row["accepted"] else 1], kind="won")
            if vote in shared_valid:
                cs.add("note", entity_id=contest, kind="context", body_md=VALID_NOTE)
            if row["provisional"]:
                cs.add("note", entity_id=contest, kind="context", body_md=PROVISIONAL_NOTE)
            if result_set in cs.minted:
                fields = [field for field in FIELDS if not (field == "gultig" and vote in shared_valid)]
                names = {"berecht": "registered", "stimmen": "cast", "gultig": "valid", "ja": "yes", "nein": "no"}
                cs.frame(
                    "result_value",
                    communes.filter(pl.col("date") == day, pl.col("vote") == vote)
                    .unpivot(on=[names[field] for field in fields], index="code", variable_name="field")
                    .drop_nulls("value")
                    .select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id=pl.col("code").replace_strict({code: str(unit) for code, unit in units.items()}),
                        option_id=pl.col("field").replace_strict(
                            {
                                names[field]: None if position is None else str(options[position])
                                for field, (_, position) in FIELDS.items()
                            },
                            return_dtype=pl.String,
                        ),
                        channel=pl.col("code").replace_strict(
                            {code: channel for code, (channel, _) in channels.items()}, return_dtype=pl.Int16
                        ),
                        measure=pl.col("field").replace_strict(
                            {names[field]: measure for field, (measure, _) in FIELDS.items()}, return_dtype=pl.Int16
                        ),
                        count_round=pl.lit(0, pl.Int16),
                        flags=pl.col("code").replace_strict(
                            {code: flags for code, (_, flags) in channels.items()}, return_dtype=pl.Int16
                        ),
                        value="value",
                    ),
                )
        print(f"{contests.height} votes on {len(elections)} days")
        return cs.write()
