import base64
import hashlib
import io
import json
import re
import subprocess
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import polars as pl
from anthropic.types.message_create_params import MessageCreateParamsNonStreaming
from PIL import Image

from ea import batch, raw
from ea.changeset import Changeset, slug
from ea.sources.us.statewide_totals import PARTIES
from ea.store import DATA, connect

SOURCE = "us.gov_official_totals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
MODEL = "claude-haiku-5-5"
PROMPT = "1"
READS = {"a": 150, "b": 200}
PAGES = Path(__file__).with_name("gov_official_totals_pages.tsv")
VERIFIED = Path(__file__).with_name("gov_official_totals_verified.tsv")
DPI = 100
PUBLISHER = "Commonwealth of Massachusetts, Secretary of the Commonwealth"
TITLE = "Manual for the Use of the General Court"
SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "candidates": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "party": {"type": "string"},
                    "votes": {"type": "string"},
                },
                "required": ["name", "party", "votes"],
                "additionalProperties": False,
            },
        },
        "printed_total": {"type": ["string", "null"]},
    },
    "required": ["candidates", "printed_total"],
    "additionalProperties": False,
}
SYSTEM = (
    "You transcribe printed election returns from scanned pages into JSON. Copy every figure exactly as printed, "
    "digits only without thousands separators or spaces. Never compute, correct, round, or fill in a number. A digit "
    "you cannot read is still your best reading of what is printed."
)
ASK = {
    "a": (
        "The page or pages show the returns for Governor in {state} in {year}. Find the statewide total for each "
        "candidate for Governor: the row labeled Total, Totals, or State Total at the bottom of the county table, or "
        "the statewide summary. Return one entry per candidate column, in column order, with the name and party as "
        "printed in the column heading and the total votes as printed. Leave out Lieutenant Governor and every other "
        "office. If a printed total of all votes for Governor is shown, return it as printed_total; otherwise null."
    ),
    "b": (
        "This is the official canvass of the {year} general election in {state}. Read the columns of the table for "
        "the office of Governor only, one column at a time. For each column give the candidate's name, the party "
        "abbreviation or name printed with it, and the figure printed on the line for the whole state's total (not a "
        "county line, not a plurality or majority line, not a percentage). Ignore Lieutenant Governor and other "
        "offices. Put the figure for all votes cast for Governor in printed_total only if the page prints one."
    ),
}


def pages() -> pl.DataFrame:
    return pl.read_csv(PAGES, separator="\t", schema_overrides={"year": pl.Int64, "rotate": pl.Int64}).with_columns(
        pl.col("rotate").fill_null(0)
    )


def find(source: str, needle: str) -> raw.Artifact:
    matches = [key for key in raw.keys(source) if needle in key]
    if len(matches) != 1:
        raise LookupError(f"{needle}: {len(matches)} matches in the {source} raw cache")
    return raw.cached(source, matches[0])


def image(path: Path, page: int, dpi: int, rotate: int) -> bytes:
    with tempfile.TemporaryDirectory() as folder:
        stem = Path(folder) / "page"
        subprocess.run(
            ["pdftoppm", "-r", str(dpi), "-gray", "-png", "-f", str(page), "-l", str(page), "-singlefile"]
            + [str(path), str(stem)],
            check=True,
        )
        with Image.open(stem.with_suffix(".png")) as rendered:
            buffer = io.BytesIO()
            gray = rendered.convert("L")
            if rotate:
                gray = gray.rotate(-rotate, expand=True)
            gray.save(buffer, "JPEG", quality=92)
    return buffer.getvalue()


def requests() -> dict[str, tuple[MessageCreateParamsNonStreaming, float]]:
    out: dict[str, tuple[MessageCreateParamsNonStreaming, float]] = {}
    for state, year, origin, needle, numbers, rotate in (
        pages().select("state", "year", "origin", "needle", "pages", "rotate").iter_rows()
    ):
        stored = find(origin, needle)
        for reader, dpi in READS.items():
            blocks: list[dict[str, object]] = []
            digest = hashlib.sha256(f"{PROMPT}{reader}{stored.sha256}{numbers}{rotate or ''}".encode()).hexdigest()
            for number in str(numbers).split(","):
                data = image(stored.path, int(number), dpi, rotate)
                blocks.append(
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": "image/jpeg",
                            "data": base64.b64encode(data).decode(),
                        },
                    }
                )
            blocks.append({"type": "text", "text": ASK[reader].format(state=state, year=year)})
            params = MessageCreateParamsNonStreaming(
                model=MODEL,
                max_tokens=4000,
                system=SYSTEM,
                messages=[{"role": "user", "content": blocks}],
                output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
            )
            out[f"anthropic-batch:{MODEL}/gov-totals/{PROMPT}/{reader}/{digest}"] = (params, 0.01)
    return out


def reads() -> dict[tuple[str, int, str], dict[str, object]]:
    found: dict[tuple[str, int, str], dict[str, object]] = {}
    wanted = requests()
    ready, note = batch.ensure(SOURCE, "reads", wanted)
    print(note)
    if not ready:
        raise SystemExit(note)
    keys = iter(wanted)
    for state, year in pages().select("state", "year").iter_rows():
        for reader in READS:
            response = json.loads(raw.cached(SOURCE, next(keys)).path.read_text())
            text = next(block["text"] for block in response["content"] if block["type"] == "text")
            found[(state, year, reader)] = json.loads(text)
    return found


def fetch() -> None:
    for url in pages()["url"].drop_nulls().unique():
        if url.startswith("inbox:"):
            try:
                raw.cached(SOURCE, url)
            except LookupError:
                raw.store(
                    SOURCE,
                    url,
                    (DATA / "inbox" / "manual" / url.removeprefix("inbox:")).read_bytes(),
                    suffix=".pdf",
                    retrieved_at=datetime.now(UTC),
                    media_type="application/pdf",
                )
        else:
            raw.fetch(SOURCE, url, suffix=".pdf")


def parse() -> None:
    PARSED.mkdir(parents=True, exist_ok=True)
    rows = []
    for (state, year, reader), body in reads().items():
        for position, entry in enumerate(body["candidates"]):
            rows.append(
                (state, year, reader, position, entry["name"], entry["party"], entry["votes"], body["printed_total"])
            )
    pl.DataFrame(
        rows, schema=["state", "year", "reader", "position", "name", "party", "votes", "printed_total"], orient="row"
    ).write_csv(PARSED / "reads.tsv", separator="\t")
    print(len(rows), "lines read")


NOTE = (
    "Statewide candidate totals as printed in the official volume, transcribed twice from the page image and "
    "checked against the printed totals and the other sources the archive holds. Lines of one candidate printed "
    "under several ticket labels are added together."
)


def surname(text: str) -> str:
    parts = [part for part in re.sub(r"[^a-z ]", "", text.lower()).split() if part not in ("jr", "sr", "iii", "ii")]
    return parts[-1] if parts else ""


def emit() -> Path:
    frame = pl.read_csv(VERIFIED, separator="\t", schema_overrides={"year": pl.Int64, "votes": pl.Int64}).with_columns(
        pl.col("party").fill_null("")
    )
    book = pages().with_columns(pl.col("year").cast(pl.Int64))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governor statewide totals for contests with only partial or no official sets, from official volumes",
            polity="US",
        )
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
        state_units = {
            code: unit
            for code, unit in conn.execute(
                """select substr(p.iso_code, 4), u.code from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%%'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        filled = 0
        for (state, year), lines in frame.group_by("state", "year", maintain_order=True):
            state = str(state)
            year = int(year)
            row = book.filter((pl.col("state") == state) & (pl.col("year") == year)).row(0, named=True)
            contest, election, ckey = conn.execute(
                """select c.id, c.election_id, min(k.value) from contest c
                   join election e on e.id = c.election_id join polity p on p.id = e.polity_id
                   join external_id o on o.entity_id = c.office_id and o.scheme = 'us.office'
                     and o.value like 'governor:%%'
                   join external_id k on k.entity_id = c.id and k.scheme = 'us.contest'
                   where p.iso_code = %s and extract(year from e.date_from) = %s group by 1, 2""",
                [f"US-{state}", year],
            ).fetchone()
            prior = conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall()
            options: dict[str, list[tuple[UUID, str]]] = {}
            for option, kind, label, current in conn.execute(
                """select o.id, o.kind::text, o.ballot_label,
                          exists (select from result_value v join result_set r on r.k = v.result_set_k
                                  where v.option_k = o.k and r.is_default) from contest_option o
                   where o.contest_id = %s order by 4 desc, o.id""",
                [contest],
            ).fetchall():
                options.setdefault(kind, []).append((option, label or ""))
            if prior and not row["publisher"]:
                source_id = prior[0][6]
            else:
                publisher = row["publisher"] or PUBLISHER
                title = row["title"] or f"{TITLE} {year + 1}"
                made = cs.source(publisher, title)
                source_id = made
                if made in cs.minted:
                    cs.add(
                        "source",
                        id=made,
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url=row["details"] or row["url"].replace("/download/", "/details/").rsplit("/", 1)[0],
                        license="public domain",
                    )
            stored = find(row["origin"], row["needle"])
            artifact = cs.artifact(stored, source_id)
            number = int(lines["page"][0])
            page = cs.page(artifact, number)
            if page in cs.minted:
                target = DATA / "pages" / SOURCE / f"{stored.path.stem}-{number}.png"
                if not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    subprocess.run(
                        ["pdftoppm", "-r", str(DPI), "-gray", "-png", "-f", str(number), "-l", str(number)]
                        + ["-singlefile", str(stored.path), str(target.with_suffix(""))],
                        check=True,
                    )
                with Image.open(target) as rendered:
                    width, height = rendered.size
                cs.add(
                    "source_page",
                    id=page,
                    artifact_id=artifact,
                    page_number=number,
                    image_key=f"pages/{SOURCE}/{target.name}",
                    width=width,
                    height=height,
                )
            result_set = cs.key(f"{SOURCE}.result_set", ckey)
            for old in prior:
                cs.add("result_set", **dict(zip(columns, old, strict=True)), is_default=False)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source_id,
                is_default=True,
                completeness=1,
                supersedes_id=prior[0][0] if prior else None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            unit = cs.unit("us.state", state_units[state])
            totals: dict[UUID, int] = {}
            taken: set[UUID] = set()
            order: list[str] = []
            sums: dict[str, tuple[str, str, int]] = {}
            for name, party, votes, kind in lines.select("name", "party", "votes", "kind").iter_rows():
                if kind == "others":
                    sums[f"{kind}:{name}"] = (name, party, votes)
                    order.append(f"{kind}:{name}")
                    continue
                if name not in sums:
                    sums[name] = (name, party, 0)
                    order.append(name)
                sums[name] = (name, sums[name][1], sums[name][2] + votes)
            for entry in order:
                name, party, votes = sums[entry]
                if entry.startswith("others:"):
                    spare = [o for o, _ in options.get("others", []) if o not in taken]
                    if spare:
                        option = spare[0]
                    else:
                        option = cs.key("us.option", f"{ckey}:official-others")
                        cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Others")
                else:
                    found = [o for o, label in options.get("candidate", []) if surname(label) == surname(name)]
                    if found:
                        option = found[0]
                    else:
                        person = cs.key(f"{SOURCE}.person", f"{state}:{slug(name)}:{year}")
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=name, lang="en", kind="official", is_preferred=True)
                        national = PARTIES.get(party.lower().removesuffix(" party"))
                        option = cs.key("us.option", f"{ckey}:{person}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=cs.key("us.party", national) if national else None,
                            ballot_label=name,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{ckey}:{person}:head"),
                            person_id=person,
                            option_id=option,
                            role="head",
                            ballot_name=name,
                            is_independent=False,
                        )
                taken.add(option)
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=8,
                    value=votes,
                )
                cs.add(
                    "value_locator",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    page_id=page,
                    source_id=None,
                    x0=0.0,
                    y0=0.0,
                    x1=1.0,
                    y1=1.0,
                )
                if not entry.startswith("others:"):
                    totals[option] = votes
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=max(totals, key=totals.__getitem__),
                kind="elected",
                seats=1,
            )
            filled += 1
        print(filled, "contests")
        return cs.write()
