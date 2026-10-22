import difflib
import re
from pathlib import Path
from types import ModuleType
from uuid import UUID

import polars as pl
from PIL import Image

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import house_books
from ea.store import DATA, connect


def surname(text: str) -> str:
    words = [w for w in re.split(r"[\s,.]+", text) if house_books._plain(w) not in {"jr", "sr", "ii", "iii", ""}]
    return house_books._plain(words[-1]) if words else ""


def parse(source: ModuleType) -> None:
    house_books.parse(source)
    parsed = raw.parsed(source.SOURCE)
    with connect() as conn:
        options = conn.execute(
            """select x.value, o.id::text, o.ballot_label, coalesce(sum(v.value), 0)::bigint
               from external_id x join contest_option o on o.contest_id = x.entity_id
               left join result_set rs on rs.contest_id = x.entity_id and rs.is_default
               left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
               where x.scheme = 'us.contest' and x.value ~ %s and o.kind in ('candidate', 'write_in')
               group by 1, 2, 3""",
            [f"^{source.DAY}:{source.STATE}:(house|senate):"],
        ).fetchall()
    book = pl.read_parquet(parsed / "values.parquet")
    rows_out: list[dict[str, object]] = []
    failed: list[str] = []
    used: set[str] = set()
    for (where, district, label), cells in book.group_by("state", "district", "label", maintain_order=True):
        kind, office = str(where).split("-")
        parties = source.PARTIES[kind]
        column = list(zip(cells["county"].to_list(), cells["votes"].to_list(), strict=True))
        places = list(zip(cells["page"].to_list(), cells["box"].to_list(), strict=True))
        pool = [
            (contest, option, name, filed)
            for contest, option, name, filed in options
            if contest.startswith(f"{source.DAY}:{source.STATE}:{office}:")
            and contest.split(":")[-1] in parties
            and (office == "senate" or contest.split(":")[3] == str(district))
        ]
        scored = sorted(
            (
                (difflib.SequenceMatcher(None, surname(str(label)), surname(name)).ratio(), contest, option, name, filed)
                for contest, option, name, filed in pool
            ),
            reverse=True,
        )
        best = list({row[2]: row for row in scored if row[0] == scored[0][0]}.values())
        if not scored or scored[0][0] < 0.75 or len({row[1] for row in best}) != len(best):
            failed.append(f"{where} {district}: no option for {label}")
            continue
        for _, contest, option, name, filed in best:
            if option in used:
                failed.append(f"{where} {district}: {label} matches {name} twice")
                continue
            used.add(option)
            summed = sum(votes for _, votes in column)
            if summed != filed and (contest, surname(name)) not in source.CHECKED:
                failed.append(f"{contest}: {name} sums to {summed:,}, the FEC has {filed:,}")
                continue
            for (county, votes), place in zip(column, places, strict=True):
                rows_out.append(
                    {
                        "contest": contest,
                        "option": option,
                        "county": county,
                        "votes": votes,
                        "page": place[0],
                        "box": place[1],
                    }
                )
    left = {contest for contest, *_ in options} - {str(row["contest"]) for row in rows_out}
    for line in failed:
        print(line)
    print(f"{len(left)} contests left out: {sorted(left)}")
    bad = {line.split(":")[0] for line in failed}
    pl.DataFrame([row for row in rows_out if row["contest"] not in bad], infer_schema_length=None).write_parquet(
        parsed / "lines.parquet"
    )


def emit(source: ModuleType) -> Path:
    frame = pl.read_parquet(raw.parsed(source.SOURCE) / "lines.parquet")
    folder = DATA / "pages" / source.SOURCE
    url, landing, _, numbers = source.VOLUMES[source.YEAR]
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=source.SOURCE,
            version=source.VERSION,
            summary=source.SUMMARY,
            polity="US",
        )
        src = cs.source(source.PUBLISHER, source.BOOK_TITLE)
        cs.add(
            "source",
            id=src,
            kind="official_body",
            publisher=source.PUBLISHER,
            title=source.BOOK_TITLE,
            url=landing,
            license=source.LICENSE,
        )
        book = raw.cached(source.RAW, url)
        artifact = cs.artifact(book, src)
        pages: dict[int, tuple[UUID, int, int]] = {}
        folder.mkdir(parents=True, exist_ok=True)
        for number in numbers:
            image = folder / f"{book.sha256}-{number}.png"
            if not image.exists():
                house_books._render(book, number, 100, image, False)
            with Image.open(image) as rendered:
                width, height = rendered.size
            pages[number] = (cs.page(artifact, number), width, height)
            cs.add(
                "source_page",
                id=pages[number][0],
                artifact_id=artifact,
                page_number=number,
                image_key=f"pages/{source.SOURCE}/{image.name}",
                width=width,
                height=height,
            )
        counties: dict[str, UUID] = {
            house_books._plain(text): unit
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like %s""",
                [f"{source.FIPS}%"],
            ).fetchall()
        }
        if len(counties) != source.COUNTIES:
            raise ValueError(f"{source.STATE} needs {source.COUNTIES} counties")
        contests: dict[str, UUID] = dict(
            conn.execute(
                """select value, entity_id from external_id
                   where scheme = 'us.contest' and value ~ %s""",
                [f"^{source.DAY}:{source.STATE}:(house|senate):"],
            ).fetchall()
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
        for (key,), lines in frame.group_by("contest", maintain_order=True):
            result_set = cs.key(f"{source.SOURCE}.result_set", str(key))
            if result_set not in cs.minted:
                continue
            contest = contests[str(key)]
            ((election,),) = conn.execute("select election_id from contest where id = %s", [contest]).fetchall()
            held = str(key) in source.NONDEFAULT
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                if not held:
                    cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=src,
                is_default=not held,
                completeness=1,
            )
            for option, county, votes, number, box in lines.select(
                "option", "county", "votes", "page", "box"
            ).iter_rows():
                plain = house_books._plain(str(county))
                unit = counties.get(plain) or next(u for name, u in counties.items() if name.startswith(plain))
                fact = {
                    "result_set_id": result_set,
                    "unit_id": unit,
                    "option_id": UUID(option),
                    "channel": 0,
                    "measure": 1,
                    "count_round": 0,
                }
                cs.add(
                    "result_value",
                    **fact,
                    flags=house_books.DIGITIZED
                    | (house_books.DERIVED if (key, county, votes) in source.DERIVED else 0),
                    value=votes,
                )
                page, width, height = pages[number]
                cs.add(
                    "value_locator",
                    **fact,
                    page_id=page,
                    source_id=None,
                    x0=box[0] / width / 3,
                    y0=box[1] / height / 3,
                    x1=box[2] / width / 3,
                    y1=box[3] / height / 3,
                )
            present = {UUID(option) for option in lines["option"].unique()}
            for option, kind, seats in [] if held else outcomes:
                if option in present:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
        return cs.write()
