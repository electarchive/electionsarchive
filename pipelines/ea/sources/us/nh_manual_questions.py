import base64
import json
import re
import subprocess

import polars as pl

from ea import raw
from ea.sources.us import nh_manual
from ea.store import DATA

SOURCE = "us.nh_manual_questions"
VERSION = "1"
QUESTIONS: dict[str, tuple[int, list[int]]] = {
    "1970-11-03": (1970, [577, 578]),
    "1972-03-07": (1972, [666]),
    "1972-11-07": (1972, [990]),
    "1974-11-05": (1974, [651, 652]),
    "1976-11-02": (1976, [725, 726]),
    "1978-11-07": (1978, [331, 332]),
    "1980-11-04": (1980, [340]),
    "1982-11-02": (1982, [277]),
    "1984-11-06": (1984, [469, 470, 471]),
    "1986-11-04": (1986, [365]),
    "1988-11-08": (1988, [375]),
    "1990-11-06": (1990, [314]),
    "1992-11-03": (1992, [486]),
    "1998-11-03": (1998, [366]),
    "2000-11-07": (2000, [439]),
    "2002-11-05": (2002, [384]),
    "2004-11-02": (2004, [468]),
    "2006-11-07": (2006, [353]),
    "2012-11-06": (2012, [487]),
}
ONLY: dict[str, list[int]] = {"1972-11-07": [0]}
NUMBER = re.compile(r"\d{1,3}(?:,\d{3})+|\d+")
READ_DPI = 150
READ_PROMPT = (
    "This is a page of the New Hampshire Manual for the General Court showing the statewide results of constitutional "
    "questions. Each question's statewide result is a line that starts with Yes, then a vote count, then No, then a "
    "vote count. Ignore the legislative votes printed in parentheses, and any recount, county, or town figures. Reply "
    "with only a JSON list of [yes, no] integer pairs, one per statewide result line, in the order they appear on the "
    "page from top to bottom. If a number cannot be read, use null in its place. Do not add or correct numbers."
)


def pairs(year: int, page: int) -> list[dict[str, object]]:
    width, height, found = nh_manual.words(nh_manual.pdf(year), page, page)[page]
    out: list[dict[str, object]] = []
    for row in nh_manual.rows({page: (width, height, found)}):
        tokens = row.words
        if (
            len(tokens) != 4
            or tokens[0].text.strip(".:").lower() != "yes"
            or tokens[2].text.strip(".:").lower() != "no"
        ):
            continue
        if not (NUMBER.fullmatch(tokens[1].text) and NUMBER.fullmatch(tokens[3].text)):
            continue
        out.append(
            {
                "page": page,
                "yes": int(tokens[1].text.replace(",", "")),
                "no": int(tokens[3].text.replace(",", "")),
                "boxes": [
                    (word.x0 / width, word.y0 / height, word.x1 / width, word.y1 / height)
                    for word in (tokens[1], tokens[3])
                ],
            }
        )
    return out


def image(year: int, page: int):
    folder = DATA / "tmp" / SOURCE
    folder.mkdir(parents=True, exist_ok=True)
    out = folder / f"{year}-{page}.png"
    if not out.exists():
        stem = folder / f".{year}-{page}"
        subprocess.run(
            ["pdftoppm", "-r", str(READ_DPI), "-png", "-singlefile", "-f", str(page), "-l", str(page)]
            + [str(nh_manual.pdf(year)), str(stem)],
            check=True,
        )
        stem.with_suffix(".png").replace(out)
    return out


def read() -> str:
    from anthropic.types.message_create_params import MessageCreateParamsNonStreaming

    from ea import batch

    price_in, price_out = batch.PRICES["claude-haiku-5-5"]
    wanted: dict[str, batch.Want] = {}
    for year, pages in QUESTIONS.values():
        for page in pages:
            params = MessageCreateParamsNonStreaming(
                model="claude-haiku-5-5",
                max_tokens=600,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image",
                                "source": {
                                    "type": "base64",
                                    "media_type": "image/png",
                                    "data": base64.b64encode(image(year, page).read_bytes()).decode(),
                                },
                            },
                            {"type": "text", "text": READ_PROMPT},
                        ],
                    }
                ],
            )
            wanted[f"read:{year}:{page}"] = ((params), (3500 * price_in + 200 * price_out) / 1e6)
    return batch.ensure(SOURCE, "question_pages", wanted)[1]


def seen(year: int, page: int) -> list[list[int | None]]:
    text = json.loads(raw.cached(SOURCE, f"read:{year}:{page}").path.read_text())["content"][0]["text"]
    found = re.search(r"\[.*\]", text, re.DOTALL)
    return json.loads(found[0]) if found else []


def agreed() -> list[tuple[str, int, dict[str, object]]]:
    out: list[tuple[str, int, dict[str, object]]] = []
    for date, (year, pages) in QUESTIONS.items():
        position = 0
        for page in pages:
            mine = pairs(year, page)
            other = seen(year, page)
            if len(mine) != len(other):
                print(f"{date} p{page}: text read {len(mine)} lines, vision read {len(other)}")
            for index, (text, vision) in enumerate(zip(mine, other, strict=False)):
                if [text["yes"], text["no"]] == vision:
                    out.append((date, position + index, text))
                else:
                    print(f"{date} p{page} #{index}: text {text['yes']}/{text['no']} vision {vision}; not loaded")
            position += max(len(mine), len(other))
    return out


def emit():
    from ea.changeset import Changeset
    from ea.store import connect

    found = agreed()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's constitutional questions, 1970-2012, statewide Yes and No totals read from the Secretary of State's Manual for the General Court",
            polity="US-NH",
        )
        unit = cs.unit("us.state", "33")
        sources: dict[int, tuple[object, object, object, str]] = {}
        pages: dict[tuple[int, int], object] = {}
        values = []
        loaded = 0
        for date, (year, _) in QUESTIONS.items():
            contests = conn.execute(
                """select e.value, c.id, c.election_id from external_id e join contest c on c.id = e.entity_id
                   where e.scheme = 'us.contest' and e.value like %s""",
                [f"{date}:NH:question:%"],
            ).fetchall()
            contests.sort(key=lambda row: int(row[0].rsplit(":", 1)[1]))
            order = ONLY.get(date)
            if order is not None:
                contests = [contests[i] for i in order]
            for place, (external, contest, election) in enumerate(contests):
                hit = next((entry for entry in found if entry[0] == date and entry[1] == place), None)
                if hit is None:
                    print(f"{date} {external}: no agreed reading")
                    continue
                if conn.execute(
                    "select 1 from result_set where contest_id = %s and is_default and status = 'active'", [contest]
                ).fetchone():
                    print(f"{external}: already has a default")
                    continue
                options = dict(
                    conn.execute(
                        "select ballot_label, id::text from contest_option where contest_id = %s", [contest]
                    ).fetchall()
                )
                if set(options) != {"Yes", "No"}:
                    print(f"{external}: options {list(options)}")
                    continue
                if year not in sources:
                    path = nh_manual.pdf(year)
                    artifact = None
                    for kind in ("_bw", "") if year != 2008 else ("",):
                        try:
                            artifact = raw.cached(nh_manual.SOURCE, nh_manual.link(year, kind))
                            break
                        except LookupError:
                            continue
                    assert artifact is not None
                    title = f"Manual for the General Court, {year + 1}"
                    source = cs.source(nh_manual.PUBLISHER, title)
                    cs.add(
                        "source",
                        id=source,
                        kind="official_body",
                        publisher=nh_manual.PUBLISHER,
                        title=title,
                        url=f"https://archive.org/details/{nh_manual.ITEMS[year]}",
                        license="Public domain",
                    )
                    sources[year] = (source, cs.artifact(artifact, source), path, artifact.sha256)
                source, artifact_id, path, sha = sources[year]
                page = int(hit[2]["page"])
                if (year, page) not in pages:
                    rendered, width, height = nh_manual.render(path, sha, page)
                    pages[(year, page)] = cs.page(artifact_id, page)
                    cs.add(
                        "source_page",
                        id=pages[(year, page)],
                        artifact_id=artifact_id,
                        page_number=page,
                        image_key=f"pages/{nh_manual.SOURCE}/{rendered.name}",
                        width=width,
                        height=height,
                    )
                result_set = cs.key(f"{SOURCE}.result_set", external)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    supersedes_id=None,
                    is_default=True,
                    completeness=1,
                )
                cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
                for label, total, box in zip(
                    ("Yes", "No"), (hit[2]["yes"], hit[2]["no"]), hit[2]["boxes"], strict=True
                ):
                    values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(unit),
                            "option_id": options[label],
                            "channel": 0,
                            "measure": 1,
                            "count_round": 1,
                            "flags": nh_manual.DIGITIZED,
                            "value": total,
                            "page_id": str(pages[(year, page)]),
                            "x0": box[0],
                            "y0": box[1],
                            "x1": box[2],
                            "y1": box[3],
                        }
                    )
                loaded += 1
        print(f"{loaded} sets, {len(values)} values")
        if values:
            frame = pl.DataFrame(
                values,
                schema_overrides={"channel": pl.Int16, "measure": pl.Int16, "count_round": pl.Int16, "flags": pl.Int16},
            )
            cs.frame("result_value", frame.drop("page_id", "x0", "y0", "x1", "y1"))
            cs.frame(
                "value_locator",
                frame.select(
                    "result_set_id",
                    "unit_id",
                    "option_id",
                    "channel",
                    "measure",
                    "count_round",
                    "page_id",
                    "x0",
                    "y0",
                    "x1",
                    "y1",
                ).with_columns(source_id=pl.lit(None, pl.String)),
            )
        return cs.write()
