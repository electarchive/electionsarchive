import html
import re
import struct
import subprocess
from bisect import bisect_right
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "it.camera"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PAGES = DATA / "pages" / SOURCE
SEDUTA = (
    "https://documenti.camera.it/leg18/resoconti/sedutaComune/pdf/sed0005/leg.18.sedutaComune.sed0005.data20220124.pdf"
)
DAYS = [date(2022, 1, day) for day in (24, 25, 26, 27, 28, 28, 29, 29)]
SWORN_IN = date(2022, 2, 3)
HEADER = 95
WORD = re.compile(r'<word xMin="([\d.]+)" yMin="([\d.]+)" xMax="([\d.]+)" yMax="([\d.]+)">(.*?)</word>')
BLOCK = re.compile(r"Comunico il risultato (?:del lavoro )?della votazione[.:](.*?)(?:Hanno preso parte|Proclamo)")
LEADER = r"[ .…:]*(\d+)"
PRESENT, VOTERS, BLANK, INVALID, VOTES = 20, 14, 6, 5, 1
DIGITIZED = 8
PERSONS = {
    "Mattarella": ("it.person", "sergio-mattarella", "Sergio Mattarella"),
    "Casellati": ("it.person", "maria-elisabetta-alberti-casellati", "Maria Elisabetta Alberti Casellati"),
    "Nordio": ("it.interno.person", "NORDIO:CARLO:1947-02-06", "Carlo Nordio"),
    "Di Matteo": ("it.person", "nino-di-matteo", "Nino Di Matteo"),
    "Crosetto": ("it.person", "guido-crosetto", "Guido Crosetto"),
    "Maddalena": ("it.person", "paolo-maddalena", "Paolo Maddalena"),
}
LICENSE = "No license stated. Official acts of the State are excluded from copyright (art. 5 of Law 633/1941)."
SOURCE_NOTE = (
    "Stenographic record of the joint sitting that elected the President of the Republic in eight ballots between "
    "January 24 and 29, 2022. Each count is as the chair announced it, with a page locator into this record."
)
METHODOLOGY = (
    "Parliament in joint session, together with 58 regional delegates, elects the President of the Republic by secret "
    "ballot (art. 83 of the Constitution). There are no formal candidacies: votes can go to any citizen aged 50 or over "
    "with full civil and political rights (art. 84). The first three ballots need two-thirds of the assembly's "
    "members. From the fourth ballot on, an absolute majority is enough. Each ballot's required number of votes is "
    "stored as the chair announced it."
)
EXTRA_BALLOT = (
    "The count found one more ballot paper than voters, most likely because one elector was handed an extra paper by "
    "mistake. The chair ruled the difference irrelevant to the result and held the ballot valid."
)
ELECTION_NOTE = (
    "Votes went to names that electors wrote on the ballot, and the record gives most of them by surname alone. Six "
    "recipients are linked to a person: Sergio Mattarella, Maria Elisabetta Alberti Casellati, Carlo Nordio, Nino Di "
    "Matteo, Guido Crosetto, and Paolo Maddalena."
)


def fetch() -> None:
    raw.fetch(SOURCE, SEDUTA, suffix=".pdf")


def parse() -> None:
    pdf = raw.cached(SOURCE, SEDUTA)
    words: list[tuple[int, float, float, float, float, str]] = []
    for number, chunk in enumerate(
        subprocess.run(
            ["pdftotext", "-bbox", str(pdf.path), "-"], check=True, capture_output=True, text=True
        ).stdout.split("<page ")[1:],
        start=1,
    ):
        size = re.match(r'width="([\d.]+)" height="([\d.]+)"', chunk)
        if size is None:
            raise ValueError(f"page {number} has no size")
        width, height = float(size[1]), float(size[2])
        words.extend(
            (number, float(x0) / width, float(y0) / height, float(x1) / width, float(y1) / height, html.unescape(text))
            for x0, y0, x1, y1, text in WORD.findall(chunk)
            if float(y0) > HEADER
        )
    starts: list[int] = []
    offset = 0
    for word in words:
        starts.append(offset)
        offset += len(word[5]) + 1
    flat = " ".join(word[5] for word in words)

    blocks = list(BLOCK.finditer(flat))
    if len(blocks) != len(DAYS):
        raise ValueError(f"found {len(blocks)} ballot results, expected {len(DAYS)}")
    values: list[dict[str, object]] = []
    ballots: list[dict[str, object]] = []
    for ballot, (block, day) in enumerate(zip(blocks, DAYS, strict=True), start=1):
        start, end = block.span(1)
        found: dict[str, re.Match[str]] = {}
        for field, pattern in (
            ("present", r"Presenti(?: e votanti)?"),
            ("voters", r"Votanti"),
            ("required", r"l’Assemblea"),
            ("dispersed", r"Voti dispersi"),
            ("blank", r"Schede bianche"),
            ("invalid", r"Schede nulle"),
        ):
            match = re.compile(pattern + LEADER).search(flat, start, end)
            if match is not None:
                found[field] = match
        found.setdefault("voters", found["present"])
        if found.keys() != {"present", "voters", "required", "dispersed", "blank", "invalid"}:
            raise ValueError(f"ballot {ballot} lacks {sorted(found)}")
        named = re.compile(r"Hanno ottenuto voti: (.*?) Voti dispersi").search(flat, start, end)
        if named is None:
            raise ValueError(f"ballot {ballot} lists no names")
        entries = [
            (re.sub(r"\s+", " ", match[1].replace("- ", "")).strip(), match)
            for match in re.compile(r"([^;,\d]+?) (\d+)[;,.]?").finditer(flat, *named.span(1))
        ]
        rows = [
            *((measure, None, found[field]) for field, measure in (("present", PRESENT), ("voters", VOTERS))),
            *((measure, None, found[field]) for field, measure in (("blank", BLANK), ("invalid", INVALID))),
            *((VOTES, label, match) for label, match in entries),
            (VOTES, "others", found["dispersed"]),
        ]
        for measure, label, match in rows:
            page, x0, y0, x1, y1, text = words[bisect_right(starts, match.start(match.lastindex or 0)) - 1]
            if text.strip(";,.") != match[match.lastindex or 0]:
                raise ValueError(f"ballot {ballot}: {text!r} is not {match[match.lastindex or 0]!r}")
            values.append(
                {
                    "ballot": ballot,
                    "measure": measure,
                    "label": label,
                    "value": int(match[match.lastindex or 0]),
                    "page": page,
                    "box": [x0, y0, x1, y1],
                }
            )
        counted = sum(int(match[2]) for _, match in entries) + sum(
            int(found[field][1]) for field in ("dispersed", "blank", "invalid")
        )
        ballots.append(
            {
                "ballot": ballot,
                "day": day,
                "required": int(found["required"][1]),
                "voters": int(found["voters"][1]),
                "counted": counted,
            }
        )
        print(ballot, day, {field: int(match[1]) for field, match in found.items()}, entries[:3], "counted", counted)
    proclaimed = re.search(r"Proclamo eletto Presidente della Repubblica (\S+ \S+)", flat)
    if proclaimed is None:
        raise ValueError("no proclamation")
    winner = max(
        (row for row in values if row["ballot"] == len(DAYS) and row["label"] not in (None, "others")),
        key=lambda row: int(str(row["value"])),
    )
    if not proclaimed[1].endswith(str(winner["label"])) or int(str(winner["value"])) < int(
        str(ballots[-1]["required"])
    ):
        raise ValueError(f"the proclaimed {proclaimed[1]} is not the last ballot's winner {winner}")

    PAGES.mkdir(parents=True, exist_ok=True)
    pages: list[dict[str, object]] = []
    for number in sorted({int(str(row["page"])) for row in values}):
        image = PAGES / f"{pdf.sha256}-{number}.png"
        if not image.exists():
            subprocess.run(
                [
                    "pdftoppm",
                    "-r",
                    "100",
                    "-png",
                    "-f",
                    str(number),
                    "-l",
                    str(number),
                    "-singlefile",
                    str(pdf.path),
                    str(image.with_suffix("")),
                ],
                check=True,
            )
        width, height = struct.unpack(">II", image.read_bytes()[16:24])
        pages.append({"page": number, "image": f"pages/{SOURCE}/{image.name}", "width": width, "height": height})

    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(ballots).write_parquet(PARSED / "ballots.parquet")
    pl.DataFrame(values, schema_overrides={"box": pl.List(pl.Float32)}).write_parquet(PARSED / "values.parquet")
    pl.DataFrame(pages).write_parquet(PARSED / "pages.parquet")
    pl.DataFrame({"name": [proclaimed[1]], "label": [winner["label"]]}).write_parquet(PARSED / "proclaimed.parquet")


def emit() -> Path:
    ballots = pl.read_parquet(PARSED / "ballots.parquet")
    values = pl.read_parquet(PARSED / "values.parquet")
    pages = pl.read_parquet(PARSED / "pages.parquet")
    winner = pl.read_parquet(PARSED / "proclaimed.parquet")["label"][0]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="2022 Italian presidential election, eight ballots in Parliament's joint session",
            polity="IT",
        )

        def name(entity: UUID, text: str, lang: str = "en", kind: str = "official") -> None:
            cs.add("name", entity_id=entity, text=text, lang=lang, kind=kind, is_preferred=kind == "official")

        publisher, title = "Camera dei deputati", "Parlamento in seduta comune, resoconto stenografico, seduta n. 5"
        camera = cs.source(publisher, title)
        cs.add(
            "source",
            id=camera,
            kind="official_body",
            publisher=publisher,
            title=title,
            url=SEDUTA,
            license=LICENSE,
            notes=SOURCE_NOTE,
        )
        artifact = cs.artifact(raw.cached(SOURCE, SEDUTA), camera)
        for number, image, width, height in pages.iter_rows():
            cs.add(
                "source_page",
                id=cs.page(artifact, number),
                artifact_id=artifact,
                page_number=number,
                image_key=image,
                width=width,
                height=height,
            )

        italy = cs.polity("IT")
        if italy in cs.minted:
            raise ValueError("publish it.interno before it.camera")
        cs.add("scheme", code="it.assembly", polity_id=italy, kind="electoral_district", rank=0, noun="assembly")
        assembly = cs.key("it.body", "presidential-electors")
        cs.add("body", id=assembly, polity_id=italy, kind="electoral_college")
        name(assembly, "Parliament in joint session with regional delegates")
        name(assembly, "Parlamento in seduta comune integrato dai delegati regionali", "it", "native")
        president = cs.key("it.office", "president")
        cs.add("office", id=president, polity_id=italy, kind="head_of_state")
        name(president, "President of the Italian Republic")
        name(president, "Presidente della Repubblica", "it", "native")
        system = cs.key("it.electoral_system", "presidential-election")
        cs.add(
            "electoral_system",
            id=system,
            polity_id=italy,
            rules={
                "formula": "repeated_ballot",
                "majorities": [{"fromRound": 1, "share": 2 / 3}, {"fromRound": 4, "share": 0.5, "strict": True}],
            },
            rules_version=1,
        )
        name(system, "Election of the President of the Italian Republic")
        cs.add("note", entity_id=system, kind="methodology", body_md=METHODOLOGY)
        election = cs.key("it.election", f"{DAYS[0]}:president")
        cs.add(
            "election",
            id=election,
            polity_id=italy,
            kind="indirect",
            date_from=DAYS[0],
            date_to=DAYS[-1],
            status="held",
        )
        name(election, "2022 Italian presidential election")
        name(election, "Elezione del Presidente della Repubblica del 2022", "it", "native")
        path = f"it/{DAYS[0]}-president"
        cs.add("slug", path=path, entity_id=election)
        cs.add("note", entity_id=election, kind="caveat", body_md=ELECTION_NOTE)
        unit = cs.unit("it.assembly", "seduta-comune")
        cs.add(
            "reporting_unit",
            id=unit,
            polity_id=italy,
            scheme_code="it.assembly",
            code="seduta-comune",
            area_relation="unlocated",
            kind="aggregate",
        )
        name(unit, "Parlamento in seduta comune", "it")

        persons: dict[str, UUID] = {}
        for label, (scheme, value, full) in PERSONS.items():
            persons[label] = cs.key(scheme, value)
            if persons[label] in cs.minted:
                if scheme != "it.person":
                    raise ValueError(f"publish it.interno before it.camera: {full} is missing")
                cs.add("person", id=persons[label])
                name(persons[label], full, "it")

        result_sets: dict[int, str] = {}
        options: dict[str, str] = {}
        previous: UUID | None = None
        last: UUID | None = None
        for ballot, day, required, voters, counted in ballots.iter_rows():
            contest_key = f"{DAYS[0]}:president:{ballot}"
            contest = cs.key("it.contest", contest_key)
            cs.add(
                "contest",
                id=contest,
                election_id=election,
                kind="office",
                office_id=president,
                body_id=assembly,
                magnitude=1,
                round_number=ballot,
                previous_round_id=previous,
                electoral_system_id=system,
                rules_override={"required": required},
                default_result_kind="counted",
            )
            name(contest, f"Ballot {ballot}, {day:%B} {day.day}")
            cs.add("slug", path=f"{path}/ballot-{ballot}", entity_id=contest)
            previous = last = contest
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                status="active",
                source_id=camera,
                is_default=True,
                completeness=1,
            )
            if result_set in cs.minted:
                result_sets[ballot] = str(result_set)
            if counted != voters:
                if counted != voters + 1:
                    raise ValueError(f"ballot {ballot} counts {counted} ballot papers for {voters} voters")
                cs.add("note", entity_id=result_set, kind="context", body_md=EXTRA_BALLOT)
            for label in values.filter(pl.col("ballot") == ballot, pl.col("label").is_not_null())["label"]:
                option = cs.key("it.option", f"{contest_key}:{slug(label)}")
                options[f"{ballot}:{label}"] = str(option)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="others" if label == "others" else "write_in",
                    ballot_label="Voti dispersi" if label == "others" else label,
                )
                if label in persons:
                    cs.add(
                        "candidacy",
                        id=cs.key("it.candidacy", f"{contest_key}:{slug(label)}"),
                        person_id=persons[label],
                        option_id=option,
                    )
                if ballot == len(DAYS) and label == winner:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected")
        cs.add(
            "tenure",
            id=cs.key(f"{SOURCE}.tenure", f"{DAYS[0]}:president"),
            person_id=persons[winner],
            office_id=president,
            from_date=SWORN_IN,
            acquired_via="elected",
            contest_id=last,
        )

        located = values.filter(pl.col("ballot").is_in(list(result_sets)))
        keys = {
            "result_set_id": pl.col("ballot").replace_strict(result_sets),
            "unit_id": pl.lit(str(unit)),
            "option_id": pl.concat_str(pl.col("ballot").cast(pl.String), pl.lit(":"), "label").replace_strict(
                options, default=None
            ),
            "channel": pl.lit(0, pl.Int16),
            "measure": pl.col("measure").cast(pl.Int16),
            "count_round": pl.lit(0, pl.Int16),
        }
        cs.frame("result_value", located.select(**keys, flags=pl.lit(DIGITIZED, pl.Int16), value="value"))
        cs.frame(
            "value_locator",
            located.select(
                **keys,
                page_id=pl.col("page").replace_strict(
                    {number: str(cs.page(artifact, number)) for number in pages["page"]}
                ),
                x0=pl.col("box").list.get(0),
                y0=pl.col("box").list.get(1),
                x1=pl.col("box").list.get(2),
                y1=pl.col("box").list.get(3),
            ),
        )
        return cs.write()
