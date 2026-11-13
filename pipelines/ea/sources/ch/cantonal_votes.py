import json
import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.ch.swisstopo import CANTONS
from ea.sources.ch.swissvotes import FIELDS, MONTHS
from ea.store import DATA, connect

SOURCE = "ch.cantonal_votes"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://ogd-static.voteinfo-app.ch/v1/ogd/sd-t-17-02-{day}-kantAbstimmung.json"
PUBLISHER = "Federal Statistical Office"
TITLE = "Kantonale Abstimmungen: Resultate (Open Government Data, voteinfo)"
DAYS = [
    date.fromisoformat(day)
    for day in [
        "2019-02-10",
        "2019-05-19",
        "2019-06-30",
        "2019-09-01",
        "2019-09-22",
        "2019-10-20",
        "2019-11-17",
        "2019-11-24",
        "2020-02-09",
        "2020-08-30",
        "2020-09-27",
        "2020-11-29",
        "2021-03-07",
        "2021-04-25",
        "2021-06-13",
        "2021-09-26",
        "2021-11-28",
        "2022-02-13",
        "2022-05-15",
        "2022-09-25",
        "2022-10-30",
        "2022-11-27",
        "2023-03-12",
        "2023-06-18",
        "2023-09-10",
        "2023-10-22",
        "2023-11-12",
        "2023-11-19",
        "2023-11-26",
        "2024-03-03",
        "2024-06-09",
        "2024-08-18",
        "2024-09-22",
        "2024-11-24",
        "2025-02-09",
        "2025-05-18",
        "2025-06-15",
        "2025-09-28",
        "2025-11-30",
        "2026-03-08",
        "2026-04-26",
        "2026-06-14",
        "2026-09-27",
    ]
]
LANGUAGES = ("de", "fr", "it", "rm")
SPOKEN = {"BE": ("de", "fr"), "FR": ("fr", "de"), "VS": ("fr", "de"), "GR": ("de", "it", "rm")}
ITALIAN = [
    "gennaio",
    "febbraio",
    "marzo",
    "aprile",
    "maggio",
    "giugno",
    "luglio",
    "agosto",
    "settembre",
    "ottobre",
    "novembre",
    "dicembre",
]
PLACEHOLDER = "Änderung des Bundesgesetzes über den zivilen Ersatzdienst (Zivildienstgesetz, ZDG)"
UNALLOCATED = 32
CODES = {1: "mandatory_referendum", 2: "optional_referendum", 3: "initiative", 4: "initiative", 5: "counter_proposal"}
PATTERNS = (
    ("tie_break", r"Stichfrage|Stichentscheid|[Qq]uestion subsidiaire|[Dd]omanda risolutiva|[Qq]uestione sussidiaria"),
    (
        "counter_proposal",
        r"Gegenvorschlag|Gegenentwurf|Volksvorschlag|[Cc]ontre-?projet|[Cc]ontroprogetto|[Pp]rojet populaire",
    ),
    ("initiative", r"[Ii]nitiative(?!s\b)|[Ii]niziativa"),
    ("mandatory_referendum", r"Verfassung|[Cc]onstitution|[Cc]ostituzione"),
)
OPTIONS = {"tie_break": ("Main proposal", "Counter-proposal")}
SOURCE_NOTE = (
    "The Federal Statistical Office collects each canton's results by commune on federal vote days and on other days "
    "that cantons report. Since March 2021 the files no longer say whether a vote was a mandatory or an optional "
    "referendum, so those questions are recorded as referendums of unstated type."
)
VALID_NOTE = (
    "The canton counts valid ballots across the initiative and its counter-proposal together, so they include "
    "ballots left blank on this question. They are not shown here."
)
SHORT_NOTE = (
    "The canton's totals are larger than the sum of its communes, and the source does not assign the difference to "
    "any commune: the canton reports {registered:,} registered voters, {cast:,} ballots cast, {yes:,} yes, and "
    "{no:,} no."
)
UNTYPED_NOTE = (
    "The source gives this vote's type as 0, so it does not say whether it was a mandatory or an optional referendum."
)
TOTALS = ["registered", "cast", "valid", "yes", "no"]


def url(day: date) -> str:
    return URL.format(day=f"{day:%Y%m%d}")


def fetch() -> None:
    for day in DAYS:
        raw.fetch(SOURCE, url(day), suffix=".json")


def kind(code: int, titles: dict[str, str]) -> str:
    text = " | ".join(titles.values())
    for name, pattern in PATTERNS:
        if re.search(pattern, text):
            return name
    return CODES.get(code, "referendum")


def parse() -> None:
    abbreviations = {abbreviation: number for number, (abbreviation, *_) in CANTONS.items()}
    contests = []
    results = []
    for day in DAYS:
        data = json.loads(raw.cached(SOURCE, url(day)).path.read_text(encoding="utf-8-sig"))
        if data["abstimmtag"] != f"{day:%Y%m%d}":
            raise ValueError(f"{day}: the file is for {data['abstimmtag']}")
        for canton in data["kantone"]:
            abbreviation = canton["geoLevelname"]
            if abbreviations.get(abbreviation) != int(canton["geoLevelnummer"]):
                raise ValueError(f"{day}: unknown canton {abbreviation}")
            for vote in canton["vorlagen"]:
                if not vote["vorlageBeendet"]:
                    raise ValueError(f"{day} {abbreviation}: vote {vote['vorlagenId']} is not finished")
                titles = {
                    title["langKey"]: re.sub(r"\s+", " ", title["text"]).strip()
                    for title in vote["vorlagenTitel"]
                    if title["text"].strip() and title["text"].strip() != PLACEHOLDER
                }
                if not titles:
                    raise ValueError(f"{day} {abbreviation}: vote {vote['vorlagenId']} has no title")
                contests.append(
                    {
                        "date": day,
                        "canton": abbreviation,
                        "vote": vote["vorlagenId"],
                        "order": vote["reihenfolgeAnzeige"],
                        "type": kind(vote["vorlagenArtId"], titles),
                        "accepted": vote["vorlageAngenommen"],
                        **{f"title_{lang}": titles.get(lang) for lang in LANGUAGES},
                    }
                )
                levels = [("canton", abbreviation, None, vote["resultat"])]
                levels.extend(
                    ("commune", f"{int(town['geoLevelnummer']):04d}", town["geoLevelname"], town["resultat"])
                    for town in vote["gemeinden"]
                )
                for level, code, label, result in levels:
                    results.append(
                        {
                            "date": day,
                            "canton": abbreviation,
                            "vote": vote["vorlagenId"],
                            "level": level,
                            "code": code,
                            "label": label,
                            "registered": result["anzahlStimmberechtigte"],
                            "cast": result["eingelegteStimmzettel"],
                            "valid": result["gueltigeStimmen"],
                            "yes": result["jaStimmenAbsolut"],
                            "no": result["neinStimmenAbsolut"],
                        }
                    )
    frame = pl.DataFrame(results, infer_schema_length=None)
    keys = ["date", "canton", "vote"]
    compared = (
        frame.filter(pl.col("level") == "commune")
        .group_by(keys)
        .agg(pl.col(TOTALS).sum())
        .join(frame.filter(pl.col("level") == "canton").select(*keys, *TOTALS), on=keys, suffix="_c")
    )
    over = compared.filter(pl.any_horizontal(pl.col(field) > pl.col(f"{field}_c") for field in TOTALS))
    if over.height:
        raise ValueError(f"{over.height} votes have communes adding to more than the canton:\n{over}")
    short = compared.filter(pl.any_horizontal(pl.col(field) != pl.col(f"{field}_c") for field in TOTALS))
    if set(short["canton"]) - {"BE"}:
        raise ValueError(f"communes fall short of the canton outside Bern:\n{short}")
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(contests, infer_schema_length=None).write_parquet(PARSED / "contests.parquet")
    frame.write_parquet(PARSED / "results.parquet")
    print(f"{len(contests)} votes on {len(DAYS)} days, {len(results)} result rows, {short.height} short of the canton")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet")
    results = pl.read_parquet(PARSED / "results.parquet")
    communes = results.filter(pl.col("level") == "commune")
    cantons = results.filter(pl.col("level") == "canton")
    keys = ["date", "canton", "vote"]
    summed = communes.group_by(keys).agg(pl.col(TOTALS).sum())
    totals = {
        (row["date"], row["canton"], row["vote"]): row
        for row in cantons.join(summed, on=keys, suffix="_s").iter_rows(named=True)
    }
    native = {abbreviation: (name, lang, english) for abbreviation, name, lang, english in CANTONS.values()}
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Swiss cantonal popular votes from February 2019 by commune (Federal Statistical Office)",
            polity="CH",
        )

        def name(entity: UUID, text: str, lang: str, kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text.strip(), lang=lang, kind=kind, is_preferred=kind == "official")

        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url="https://opendata.swiss/de/dataset/echtzeitdaten-am-abstimmungstag-zu-kantonalen-abstimmungsvorlagen",
                license="Open use; must provide the source; commercial use requires the data owner's permission",
                license_url="https://opendata.swiss/en/terms-of-use#terms_by_ask",
                notes=SOURCE_NOTE,
            )
        for day in DAYS:
            cs.artifact(raw.cached(SOURCE, url(day)), source)

        polities = {abbreviation: cs.polity(f"CH-{abbreviation}") for abbreviation in native}
        system = cs.key("ch.electoral_system", "cantonal-referendum")
        if system in cs.minted:
            cs.add(
                "electoral_system",
                id=system,
                polity_id=cs.polity("CH"),
                rules={"formula": "referendum"},
                rules_version=1,
            )
            name(system, "Swiss cantonal referendum", "en")
        units: dict[str, UUID] = {}
        channels: dict[str, tuple[int, int]] = {}
        for code, label in communes.select("code", "label").unique("code").sort("code").iter_rows():
            units[code] = cs.unit("ch.commune", code)
            if units[code] in cs.minted:
                raise ValueError(f"commune {code} ({label}) has no unit: run ch.bfs_votes first")
            channels[code] = (4, UNALLOCATED) if code >= "9000" else (0, 0)

        elections: dict[tuple[date, str], UUID] = {}
        parts: dict[tuple[date, str], set[str]] = {}
        for row in contests.sort("date", "canton", "order", "vote").iter_rows(named=True):
            day: date = row["date"]
            canton: str = row["canton"]
            vote: int = row["vote"]
            local, lang, english = native[canton]
            if (day, canton) not in elections:
                election = cs.key("ch.election", f"{canton}:{day}")
                elections[day, canton] = election
                parts[day, canton] = set()
                count = contests.filter(pl.col("date") == day, pl.col("canton") == canton).height
                cs.add(
                    "election",
                    id=election,
                    polity_id=polities[canton],
                    kind="referendum",
                    date_from=day,
                    date_to=day,
                    status="held",
                )
                label = "referendums" if count > 1 else "referendum"
                name(
                    election,
                    f"{english or local} cantonal {label} of {MONTHS['en'][day.month - 1]} {day.day}, {day.year}",
                    "en",
                )
                for spoken in SPOKEN.get(canton, (lang,)):
                    if spoken == "de":
                        text = f"Kantonale Volksabstimmung vom {day.day}. {MONTHS['de'][day.month - 1]} {day.year}"
                    elif spoken == "fr":
                        first = "1er" if day.day == 1 else str(day.day)
                        text = f"Votation cantonale du {first} {MONTHS['fr'][day.month - 1]} {day.year}"
                    elif spoken == "it":
                        text = f"Votazione cantonale del {day.day} {ITALIAN[day.month - 1]} {day.year}"
                    else:
                        continue
                    name(election, text, spoken, "native")
                cs.add("slug", path=f"ch/{canton.lower()}/{day}-referendum", entity_id=election)
            election = elections[day, canton]
            contest_key = f"voteinfo:{canton}:{vote}"
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
            kind = row["type"]
            cs.add("ballot_question", contest_id=contest, type=kind)
            titles = {lang: row[f"title_{lang}"] for lang in LANGUAGES if row[f"title_{lang}"] is not None}
            primary = lang if lang in titles else next(iter(titles))
            for spoken, text in titles.items():
                name(contest, text, spoken, "official" if spoken == primary else "native")
            part = slug(titles[primary])
            if len(part) > 60:
                part = part[:61].rsplit("-", 1)[0]
            part = part or f"vote-{vote}"
            if part in parts[day, canton]:
                part = f"{part}-{vote}"
            parts[day, canton].add(part)
            cs.add("slug", path=f"ch/{canton.lower()}/{day}-referendum/{part}", entity_id=contest)
            labels = OPTIONS.get(kind, ("Yes", "No"))
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
            total = totals[day, canton, vote]
            complete = all(total[field] == total[f"{field}_s"] for field in TOTALS)
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
                completeness=1 if complete else round(total["cast_s"] / total["cast"], 4),
            )
            if kind != "tie_break":
                cs.add("outcome", result_set_id=result_set, option_id=options[0 if row["accepted"] else 1], kind="won")
            shared = total["valid"] != total["yes"] + total["no"]
            if kind == "referendum":
                cs.add("note", entity_id=contest, kind="context", body_md=UNTYPED_NOTE)
            if shared:
                cs.add("note", entity_id=contest, kind="context", body_md=VALID_NOTE)
            if not complete:
                cs.add("note", entity_id=contest, kind="context", body_md=SHORT_NOTE.format(**total))
            if result_set in cs.minted:
                fields = [field for field in FIELDS if not (field == "gultig" and shared)]
                names = {"berecht": "registered", "stimmen": "cast", "gultig": "valid", "ja": "yes", "nein": "no"}
                cs.frame(
                    "result_value",
                    communes.filter(pl.col("date") == day, pl.col("canton") == canton, pl.col("vote") == vote)
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
        print(f"{contests.height} votes in {len(elections)} cantonal vote days")
        return cs.write()
