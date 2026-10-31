import difflib
import os
import re
import subprocess

import polars as pl

from ea.sources.us import nh_manual
from ea.sources.us.nh_sos_county import COLUMNS
from ea.store import DATA

SOURCE = "us.nh_manual_state"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
OFFICES = (
    (r"governor", "governor"),
    (r"united\s+states\s+senators?", "us senate"),
    (r"representatives?\s+in\s+congress", "us house"),
    (r"(?:executive\s+)?councill?ors?", "executive council"),
    (r"state\s+senat\w*", "state senate"),
)
UNITS = [
    "first",
    "second",
    "third",
    "fourth",
    "fifth",
    "sixth",
    "seventh",
    "eighth",
    "ninth",
    "tenth",
    "eleventh",
    "twelfth",
    "thirteenth",
    "fourteenth",
    "fifteenth",
    "sixteenth",
    "seventeenth",
    "eighteenth",
    "nineteenth",
]
ORDINALS = {
    **{word: number for number, word in enumerate(UNITS, 1)},
    "twentieth": 20,
    **{f"twenty{word}": 20 + number for number, word in enumerate(UNITS[:9], 1)},
}


def heading(text: str) -> tuple[str, str] | None:
    plain = re.sub(r"\s+", " ", text).strip()
    found = re.match(r"For\s+(?:the\s+)?(.*?)[:;.]?$", plain, re.IGNORECASE)
    if found:
        for pattern, name in OFFICES:
            if re.fullmatch(pattern, found[1].strip(), re.IGNORECASE):
                return "office", name
        return "other", plain
    if re.search(r"COUNTY\s+OFFIC|^[A-Z]{4,12}\s+COUNTY:?$", plain):
        return "other", plain
    digit = re.fullmatch(r"district\s+(?:no\.?\s*)?(\d{1,2})\s*:?", plain, re.IGNORECASE)
    if digit:
        return "district", digit[1]
    word = re.fullmatch(r"([A-Za-z1.\\]+(?:[\s-]+[A-Za-z.]+)?)\s+D[a-z][A-Za-z.]{3,8}\s*[:;]?", plain)
    if word:
        match = difflib.get_close_matches(re.sub(r"[^a-z]", "", word[1].lower()), ORDINALS, n=1, cutoff=0.8)
        if match:
            return "district", str(ORDINALS[match[0]])
    return None


def summaries(path) -> list[tuple[str, list[int]]]:
    layout = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
    pages = layout.split("\f")
    starts = [number for number, text in enumerate(pages, 1) if re.search(r"(?im)^\s*For\s+Governor\s*:?\s*$", text)]
    out = []
    for start in starts:
        end = start
        while end < min(start + 12, len(pages)) and not re.search(
            r"(?m)^\s*COUNTY OFFIC|^\s*(?i:For\s+(the\s+)?Sheriff)", pages[end - 1]
        ):
            end += 1
        primary = bool(re.search(r"(?i)total\s+vote\s*,\s*[rd]\b", "\n".join(pages[start - 1 : end])))
        out.append(("primary" if primary else "general", list(range(start, end + 1))))
    return out


def mended(rows: list[nh_manual.Row]) -> list[nh_manual.Row]:
    out = []
    for row in rows:
        words = list(row.words)
        if len(words) > 1 and words[0].text in ("I", "1", "l") and words[1].text == "otal":
            words = [words[0]._replace(text="Total", x1=words[1].x1), *words[2:]]
        if words and re.fullmatch(r"[fl1I]otal", words[0].text):
            words[0] = words[0]._replace(text="Total")
        out.append(row._replace(words=words))
    return out


def parse() -> None:
    frames = []
    for year in nh_manual.years():
        try:
            path = nh_manual.pdf(year)
        except LookupError:
            print(f"{year}: not fetched")
            continue
        found = summaries(path)
        print(year, [(stage, pages[0], pages[-1]) for stage, pages in found])
        for stage, pages in found:
            page_words = nh_manual.words(path, pages[0], pages[-1])
            frame = pl.DataFrame(
                nh_manual.lines(mended(nh_manual.rows(page_words)), heading=heading), infer_schema_length=None
            )
            if frame.height:
                frames.append(frame.with_columns(year=pl.lit(year), stage=pl.lit(stage)))
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.concat(frames, how="diagonal_relaxed").write_parquet(PARSED / "lines.parquet")


SUPERSEDES = ("us.wikipedia_gov_history", "us.klarner")
TOWN = re.compile(r"(.+[a-z])[.,]\s+([A-Z][a-z]+(?: [A-Z][a-z]+)?)")


def arbitrated(frame: pl.DataFrame) -> pl.DataFrame:
    frame = nh_manual.verified(frame.drop("added", "agrees", strict=False))
    swaps: dict[int, int] = {}
    for _, rows in frame.filter(pl.col("printed").is_not_null() & ~pl.col("agrees")).group_by("year", "stage", "group"):
        gap = int(rows["added"][0]) - int(rows["printed"][0])
        fits = []
        for index, votes, digits in rows.select("index", "votes", "digits").iter_rows():
            tokens = (digits or "").split()
            if len(tokens) > 1 and re.fullmatch(r"\d{1,3}(?:,\d{3})*", tokens[-1]):
                alone = int(tokens[-1].replace(",", ""))
                if votes - alone == gap:
                    fits.append((index, alone))
        if len(fits) == 1:
            swaps[fits[0][0]] = fits[0][1]
    frame = frame.with_columns(
        votes=pl.col("index").replace_strict(swaps, default=pl.col("votes"), return_dtype=pl.Int64),
        clean=pl.col("clean") & ~pl.col("index").is_in(list(swaps)),
    )
    frame = nh_manual.blocks(frame.drop("added", "agrees", "printed_right", strict=False))
    cut = pl.col("digits").str.starts_with(",").any().over("year", "stage", "group")
    return frame.with_columns(agrees=pl.when(cut).then(False).otherwise(pl.col("agrees")))


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def split(name: str, town: str | None) -> tuple[str, str | None]:
    if town and re.fullmatch(r"(?i)(?:r|d|l|lib|ind)(?:\s*(?:and|&)\s*(?:r|d|l|lib|ind))*", town) and "," in name:
        name, town = (part.strip() for part in name.rsplit(",", 1))
    found = TOWN.fullmatch(name) if town is None else None
    if found and not re.fullmatch(r"(?:Jr|Sr|II|III|IV)", found[2]):
        return found[1], found[2]
    return name, town


def suffix(office: str, district: str | None, stage: str, side: str | None) -> str | None:
    party = {"r": "republican", "d": "democratic"}.get(side or "")
    if office == "governor":
        return "governor" if stage == "general" else f"governor:{party}"
    if office == "us senate":
        return "senate" if stage == "general" else f"senate:{party}"
    if office == "us house" and district:
        return f"house:{district}" if stage == "general" else f"house:{district}:{party}"
    if office == "state senate" and district:
        return f"senate:{district}:g" if stage == "general" else f"senate:{district}:{side}p"
    return None


def emit():
    from ea import raw
    from ea.changeset import Changeset, slug
    from ea.store import connect

    lines = arbitrated(
        pl.read_parquet(PARSED / "lines.parquet").with_row_index("index").with_columns(pl.col("index").cast(pl.Int64))
    )
    lines = (
        lines.with_columns(
            pl.struct("name", "town")
            .map_elements(
                lambda row: dict(zip(("name", "town"), split(row["name"], row["town"]), strict=True)),
                return_dtype=pl.Struct({"name": pl.String, "town": pl.String}),
            )
            .alias("fixed")
        )
        .drop("name", "town")
        .unnest("fixed")
    )
    only = os.environ.get("NH_YEARS")
    if only:
        lines = lines.filter(pl.col("year").is_in([int(year) for year in only.split(",")]))
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="New Hampshire's governor and State Senate returns, 1970-2014, read from the statewide summaries in the Secretary of State's Manual for the General Court",
            polity="US-NH",
        )
        contests: dict[tuple[int, str, str], tuple[str, object]] = {}
        for value, contest in conn.execute(
            """select value, entity_id from external_id
               where scheme = 'us.contest' and value ~ '^(19[7-9]\\d|200\\d|201[0-4])-(09|11)-\\d\\d:NH:'"""
        ).fetchall():
            month = value[5:7]
            contests[(int(value[:4]), "general" if month == "11" else "primary", value[14:])] = (value, contest)
        sources: dict[int, tuple[object, object, object, str]] = {}
        pages: dict[tuple[int, int], object] = {}
        report: dict[str, list[str]] = {}
        values = []
        keyed = lines.group_by("year", "stage", "office", "district", "side", maintain_order=True)
        for (year, stage, office, district, side), rows in keyed:
            tag = f"{year} {stage} {office} {district or ''} {side or ''}".strip()
            wanted = suffix(str(office), district, str(stage), side)
            found = contests.get((int(year), str(stage), wanted or ""))
            if found is None:
                report.setdefault("no contest", []).append(tag)
                continue
            external, contest = found
            if rows.filter(pl.col("agrees").is_null()).height:
                report.setdefault("no printed total", []).append(tag)
                continue
            if not rows["agrees"].all():
                report.setdefault("lines do not add to the printed total", []).append(tag)
                continue
            default = conn.execute(
                """select rs.id, c.pipeline, rs.election_id, min(v.count_round), array_agg(distinct v.unit_k)
                   from result_set rs join changeset c on c.id = rs.changeset_id
                   left join result_value v on v.result_set_k = rs.k
                   where rs.contest_id = %s and rs.is_default and rs.status = 'active' group by 1, 2, 3""",
                [contest],
            ).fetchone()
            if default is not None and default[1] not in SUPERSEDES:
                report.setdefault(f"kept {default[1]}", []).append(tag)
                continue
            election = (
                default[2]
                if default
                else conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
            )
            first = (default[3] if default else None) or 1
            if default is not None:
                units = [unit for unit in default[4] if unit is not None]
                if len(units) != 1:
                    report.setdefault("default has more than one unit", []).append(tag)
                    continue
                unit = conn.execute("select id from reporting_unit where k = %s", [units[0]]).fetchone()[0]
            elif office in ("governor", "us senate"):
                unit = cs.unit("us.state", "33")
            else:
                report.setdefault("no unit", []).append(tag)
                continue
            options = conn.execute(
                """select o.id::text, o.ballot_label, o.kind,
                          coalesce((select sum(v.value) from result_value v join result_set rs on rs.k = v.result_set_k
                                    where rs.id = %s and v.option_k = o.k and v.measure = 1), 0)::bigint
                   from contest_option o where o.contest_id = %s and o.parent_option_id is null""",
                [default[0] if default else None, contest],
            ).fetchall()
            others = [option[0] for option in options if option[2] in ("others", "write_in")]
            totals = rows.group_by("name", maintain_order=True).agg(
                pl.col("votes").sum(), pl.col("town").drop_nulls().first(), pl.col("writein").any()
            )
            matched: dict[str, str | None] = {}
            fresh: list[str] = []
            failed = None
            for name, _, town, writein in totals.iter_rows():
                mine = nh_manual.person(name)
                if name == "Scatter" or re.fullmatch(r"(?i)scatter\w*|write-?ins?", name):
                    matched[name] = None
                    continue
                hits = [
                    option
                    for option in options
                    if option[2] == "candidate"
                    and (
                        nh_manual.person(option[1])[0] == mine[0]
                        or len(mine[0]) > 4
                        and difflib.SequenceMatcher(None, nh_manual.person(option[1])[0], mine[0]).ratio() >= 0.8
                        and nh_manual.person(option[1])[1] == mine[1]
                    )
                ]
                if len(hits) > 1:
                    hits = [hit for hit in hits if plain(hit[1]) == plain(name)] or hits
                if len(hits) > 1:
                    hits = [hit for hit in hits if nh_manual.person(hit[1])[1] == mine[1]] or hits
                if len(hits) == 1:
                    matched[name] = hits[0][0]
                elif hits:
                    failed = f"{name} matches {[hit[1] for hit in hits]}"
                    break
                elif stage == "primary" or writein:
                    matched[name] = None
                elif town and nh_manual.CLEAN.fullmatch(name) and len(mine[0]) > 2:
                    fresh.append(name)
                else:
                    failed = f"{name} matches nothing"
                    break
            idle = [
                option[1]
                for option in options
                if option[2] == "candidate" and option[0] not in matched.values() and (default is None or option[3])
            ]
            loose = [name for name, hit in matched.items() if hit is None and name != "Scatter"] + fresh
            if not failed and len(idle) == 1 and len(loose) == 1:
                option = next(o for o in options if o[1] == idle[0] and o[2] == "candidate")
                if difflib.SequenceMatcher(None, plain(option[1]), plain(loose[0])).ratio() >= 0.75:
                    matched[loose[0]] = option[0]
                    fresh = [name for name in fresh if name != loose[0]]
                    idle, loose = [], []
            if not failed and idle and loose:
                failed = f"unmatched lines {loose} and unmatched options {idle}"
            if not failed and options and not [m for m in matched.values() if m and m not in others]:
                failed = f"no line matches the contest's options: {list(totals['name'])}"
            if not failed and None in matched.values() and len(others) > 1:
                failed = "more than one others option"
            if failed:
                report.setdefault("candidate mismatch", []).append(f"{tag} {external}: {failed}")
                continue
            added = []
            if None in matched.values():
                if not others:
                    option = cs.key("us.option", f"{external}:others")
                    added.append(("others", "Others", option))
                    others.append(str(option))
                matched = {name: hit or others[0] for name, hit in matched.items()}
            for name in fresh:
                option = cs.key("us.option", f"{external}:{slug(name)}")
                added.append(("candidate", name, option))
                matched[name] = str(option)
            ours: dict[str, int] = {}
            for name, votes in totals.select("name", "votes").iter_rows():
                ours[str(matched[name])] = ours.get(str(matched[name]), 0) + votes
            theirs = {option[0]: option[3] for option in options if option[3]}
            if default is not None and any(ours.get(o, 0) != theirs.get(o, 0) for o in {*ours, *theirs}):
                gap = {
                    label: (theirs.get(oid, 0), ours.get(oid, 0))
                    for oid, label, _, _ in options
                    if theirs.get(oid, 0) != ours.get(oid, 0)
                }
                report.setdefault(f"differs from {default[1]}", []).append(f"{tag} {external} {gap}")
            declared = (
                conn.execute(
                    "select option_id::text, kind, seats from outcome where result_set_id = %s", [default[0]]
                ).fetchall()
                if default is not None
                else []
            )
            winners = {option for option, kind, _ in declared if kind in ("elected", "won")}
            ranked = sorted(((votes, option) for option, votes in ours.items() if option not in others), reverse=True)
            if winners and (
                len(winners) > len(ranked)
                or {option for votes, option in ranked if votes >= ranked[len(winners) - 1][0]} != winners
            ):
                report.setdefault("declared winner is not the top vote-getter", []).append(tag)
                continue
            for offset, (kind, name, option) in enumerate(added):
                if kind == "others":
                    cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Others")
                    continue
                human = cs.key(f"{SOURCE}.person", f"{external}:{slug(name)}")
                cs.add("person", id=human)
                cs.add("name", entity_id=human, text=name, lang="en", kind="official", is_preferred=True)
                cs.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    ballot_position=len(options) + offset + 1,
                    ballot_label=name,
                )
                cs.add(
                    "candidacy",
                    id=cs.key("us.candidacy", f"{external}:{slug(name)}:head"),
                    person_id=human,
                    option_id=option,
                    role="head",
                    ballot_name=name,
                    is_independent=False,
                )
                report.setdefault("option added from the manual", []).append(f"{tag} {name}")
            if int(year) not in sources:
                path = nh_manual.pdf(int(year))
                artifact = None
                for kind in ("_bw", "") if year != 2008 else ("",):
                    try:
                        artifact = raw.cached(nh_manual.SOURCE, nh_manual.link(int(year), kind))
                        break
                    except LookupError:
                        continue
                assert artifact is not None
                title = f"Manual for the General Court, {int(year) + 1}"
                source = cs.source(nh_manual.PUBLISHER, title)
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=nh_manual.PUBLISHER,
                    title=title,
                    url="https://scholars.unh.edu/court/61/"
                    if year == 2008
                    else f"https://archive.org/details/{nh_manual.ITEMS[int(year)]}",
                    license="Public domain",
                )
                sources[int(year)] = (source, cs.artifact(artifact, source), path, artifact.sha256)
            source, artifact_id, path, sha = sources[int(year)]
            result_set = cs.key(f"{SOURCE}.result_set", external)
            if default is not None:
                old = dict(
                    zip(
                        COLUMNS,
                        conn.execute(
                            f"select {', '.join(COLUMNS)} from result_set where id = %s", [default[0]]
                        ).fetchone(),
                        strict=True,
                    )
                )
                cs.add("result_set", **old, status="superseded", is_default=False)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                supersedes_id=default[0] if default else None,
                is_default=True,
                completeness=1,
            )
            cs.add("external_id", entity_id=result_set, scheme="ea.authoritative", value=str(result_set))
            for option, kind, seats in declared:
                cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            for option, votes in ours.items():
                mine = rows.filter(pl.col("name").is_in([name for name, hit in matched.items() if hit == option]))
                first_page = int(mine["page"][0])
                mine = mine.filter(pl.col("page") == first_page)
                if (int(year), first_page) not in pages:
                    image, width, height = nh_manual.render(path, sha, first_page)
                    page = cs.page(artifact_id, first_page)
                    pages[(int(year), first_page)] = page
                    cs.add(
                        "source_page",
                        id=page,
                        artifact_id=artifact_id,
                        page_number=first_page,
                        image_key=f"pages/{nh_manual.SOURCE}/{image.name}",
                        width=width,
                        height=height,
                    )
                values.append(
                    {
                        "result_set_id": str(result_set),
                        "unit_id": str(unit),
                        "option_id": option,
                        "channel": 0,
                        "measure": 1,
                        "count_round": first,
                        "flags": nh_manual.DIGITIZED | (nh_manual.DERIVED if mine.height > 1 else 0),
                        "value": votes,
                        "page_id": str(pages[(int(year), first_page)]),
                        "x0": float(mine["x0"].min()),
                        "y0": float(mine["y0"].min()),
                        "x1": float(mine["x1"].max()),
                        "y1": float(mine["y1"].max()),
                    }
                )
        for reason, items in report.items():
            print(f"{len(items)}: {reason}")
            for item in items[: int(os.environ.get("NH_SHOW", "25"))]:
                print("   ", item)
        print(
            f"{len(values)} values in {len(cs.rows.get('result_set', [])) - len([r for r in cs.rows.get('result_set', []) if r.get('status') == 'superseded'])} sets"
        )
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
