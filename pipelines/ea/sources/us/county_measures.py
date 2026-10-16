import io
import json
import re
import unicodedata
from datetime import date
from pathlib import Path
from urllib.parse import urlencode
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import openelections, wikipedia_measures
from ea.store import DATA, connect

SOURCE = "us.county_measures"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
YEARS = (2016, 2018, 2020, 2022)
ARCHIVES = wikipedia_measures.ARCHIVES
COMMITS = {state: commit for state, commit in openelections.COMMITS.items() if state not in ARCHIVES} | {
    "DC": "83a229de498cb90c503f9c67575a76f84c705ad8",
    "FL": "cc8116224e23e9342658eda5d9b0b76cbf3c2912",
    "MD": "c0683efed91d246d2d3d86d6ef599624cd21f58d",
    "ME": "55ee677d8f0a36564e1a234399d5136441bdafe6",
    "MS": "c638c3b97e95fe03a8d795eef59c399766e64de1",
    "NC": "a129ab6d2fca3c481fa3eafbe660c6530904ee18",
}
MEDSL = "us.medsl_measures"
DATAVERSE = "https://dataverse.harvard.edu/api/access/datafile/"
MEDSL_2016 = f"{DATAVERSE}3345337?format=original"
MEDSL_2022 = {
    "AK": "10855159",
    "AL": "10855153",
    "CA": "10855141",
    "DC": "10855175",
    "DE": "10855156",
    "FL": "10855171",
    "GA": "10855164",
    "HI": "10855177",
    "IA": "10855151",
    "IL": "10855148",
    "KS": "10855161",
    "KY": "10855135",
    "LA": "10855157",
    "ME": "10855138",
    "MN": "10855150",
    "MO": "10855176",
    "MS": "10855162",
    "MT": "10855149",
    "NC": "10855142",
    "NE": "10855155",
    "NJ": "10855173",
    "NV": "10855182",
    "OH": "10855174",
    "OK": "10855147",
    "OR": "10855146",
    "PA": "10855169",
    "RI": "10855163",
    "SC": "10855139",
    "SD": "10855168",
    "TN": "10855152",
    "TX": "10855154",
    "UT": "10855134",
    "WI": "10855181",
    "WV": "10855172",
    "WY": "10855165",
}
MEDSL_TITLES = {
    2016: ("State Precinct-Level Returns 2016", "https://doi.org/10.7910/DVN/GSZG1O"),
    2022: ("Precinct-Level Returns 2022 by Individual State", "https://doi.org/10.7910/DVN/UYQIEP"),
}
MEDSL_PUBLISHER = "MIT Election Data and Science Lab"
OE_PUBLISHER = "OpenElections"
OE_TITLE = "OpenElections certified results"
AFFIRMATIVE = r"(?i)^\s*(YES|FOR|APPROVE|SI)\b"
NEGATIVE = r"(?i)^\s*(NO|AGAINST|REJECT)\b"
SPOILED = {"overvotes": "invalid", "over votes": "invalid", "undervotes": "blank", "under votes": "blank"}
MEASURES = {"YES": 1, "NO": 1, "blank": 6, "invalid": 5}
EXCLUDED = r"(?i)retain|retention|judge|court|justice|unopposed|school board|council"
QUESTIONS = (
    r"(?i)amendment|proposition|question|measure|issue|initiative|referendum|proposal|bond|constitution|advisory|"
    r"joint resolution|^\d+[:.]\s|\b(?:C|CI|LR|I|IR)-\d"
)
DAYS = {year: date(year, 11, 2 + (-date(year, 11, 1).weekday()) % 7) for year in YEARS}
WIKIPEDIA_2022 = wikipedia_measures.API + urlencode(
    {
        "action": "parse",
        "page": "2022 United States ballot measures",
        "prop": "wikitext",
        "format": "json",
        "formatversion": 2,
        "redirects": 1,
    }
)
TOKEN = re.compile(
    r"(?i)\b(?:proposition|amendment|question|measure|issue|initiative|referendum|proposal|number|no\.?|#)"
    r"\s*(?:no\.?|#)?\s*(\d+|[A-Z])\b|\b(?:C|CI|LR|I|IR)-(\d+)"
)
WORDS = {
    word: index + 1
    for index, word in enumerate(("ONE", "TWO", "THREE", "FOUR", "FIVE", "SIX", "SEVEN", "EIGHT", "NINE", "TEN"))
}
SMALL = {"a", "an", "and", "as", "at", "by", "for", "from", "in", "of", "on", "or", "the", "to", "with"}
ATTACHED = (
    "County totals are the sums of the {source} county or precinct rows for this question. They attach to the "
    "statewide totals because both answers match within 2 percent."
)
CREATED = (
    "County totals are the sums of the {source} county or precinct rows for this question. The outcome is the one "
    "the English Wikipedia's list of 2022 ballot measures records{fallback}."
)
FALLBACK = (
    "; this question is missing from that list, so the answer shown as winning is the one with more votes, which may "
    "not reflect a supermajority rule"
)


def tree(state: str) -> str:
    return openelections.TREE.format(state=state.lower(), commit=COMMITS[state])


def picks() -> list[tuple[str, int, str]]:
    chosen: list[tuple[str, int, str]] = []
    for state in COMMITS:
        entries = json.loads(raw.cached(SOURCE, tree(state)).path.read_text())["tree"]
        best: dict[int, tuple[int, list[str]]] = {}
        for entry in entries:
            found = re.fullmatch(
                rf"(?:.*/)?(\d{{4}})11\d{{2}}__{state.lower()}__general(__county|__precinct|__ward|__[a-z_]+__precinct)?\.csv",
                entry["path"],
            )
            if entry["type"] != "blob" or found is None or int(found[1]) not in YEARS:
                continue
            year, level = int(found[1]), openelections.LEVELS.get(found[2] or "", 4)
            if year not in best or level < best[year][0]:
                best[year] = (level, [entry["path"]])
            elif level == best[year][0] == 4:
                best[year][1].append(entry["path"])
        chosen.extend((state, year, path) for year, (_, paths) in sorted(best.items()) for path in paths)
    return chosen


def repository(state: str, path: str) -> str:
    return openelections.REPOSITORY.format(state=state.lower(), commit=COMMITS[state], path=path)


def fetch() -> None:
    raw.fetch(SOURCE, WIKIPEDIA_2022, suffix=".json")
    for state in COMMITS:
        raw.fetch(SOURCE, tree(state), suffix=".json")
    for state, _, path in picks():
        raw.fetch(SOURCE, repository(state, path), suffix=".csv")
    raw.fetch(MEDSL, MEDSL_2016, suffix=".csv")
    for file in MEDSL_2022.values():
        raw.fetch(MEDSL, f"{DATAVERSE}{file}?format=original", suffix=".csv")


def token(text: str) -> str | None:
    text = re.sub(r"\b\d{2}-(\d+)\b", r"\1", text)
    for word, number in WORDS.items():
        text = re.sub(rf"\b{word}\b", str(number), text, flags=re.IGNORECASE)
    found = TOKEN.search(text)
    return None if found is None else (found[1] or found[2]).upper()


def questions(frame: pl.DataFrame, counties: int) -> pl.DataFrame:
    answer = (
        pl.when(pl.col("candidate").str.contains(AFFIRMATIVE))
        .then(pl.lit("YES"))
        .when(pl.col("candidate").str.contains(NEGATIVE))
        .then(pl.lit("NO"))
        .otherwise(pl.col("candidate").str.to_lowercase().str.strip_chars().replace_strict(SPOILED, default=None))
    )
    return (
        frame.with_columns(answer=answer)
        .filter(
            pl.col("code").is_not_null(),
            pl.col("votes").is_not_null(),
            pl.col("office").str.contains(QUESTIONS),
            ~pl.col("office").str.contains(EXCLUDED) | pl.col("office").str.contains(r"(?i)amendment|proposition"),
            (pl.col("answer") == "YES").any().over("office"),
            (pl.col("answer") == "NO").any().over("office"),
            pl.col("answer").is_not_null(),
        )
        .filter(pl.col("code").n_unique().over("office") * 2 >= counties)
        .group_by("office", "code", "answer")
        .agg(pl.col("votes").sum(), pl.col("candidate").mode().first().alias("label"))
    )


def parse() -> None:
    with connect() as conn:
        fips: dict[str, dict[str, str]] = {}
        sizes: dict[str, int] = {}
        for code, text, iso in conn.execute(
            """select u.code, n.text, p.iso_code from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               join reporting_unit s on s.scheme_code = 'us.state' and s.code = left(u.code, 2)
               join polity p on p.id = s.polity_id
               where u.scheme_code = 'us.county' and u.code <> '51515'
                 and (u.valid_to is null or u.valid_to > '2016-01-01')"""
        ).fetchall():
            state = iso.removeprefix("US-")
            names = fips.setdefault(state, {})
            plain = re.sub(
                r"[^a-z]",
                "",
                unicodedata.normalize("NFKD", text).lower().replace("saint ", "st ").replace("&", "and"),
            )
            if int(code[2:]) >= 500:
                names[f"{plain}city"] = code
            names.setdefault(plain, code)
            sizes[state] = sizes.get(state, 0) + 1
        for (state, typo), plain in openelections.MISSPELLED.items():
            if state in fips:
                fips[state][typo] = fips[state][plain]
        known = {code for names in fips.values() for code in names.values()}
    found: dict[tuple[str, int], tuple[str, pl.DataFrame]] = {}
    grouped: dict[tuple[str, int], list[pl.DataFrame]] = {}
    for state, year, path in picks():
        text = raw.cached(SOURCE, repository(state, path)).path.read_bytes()
        frame = pl.read_csv(
            io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
            encoding="utf8-lossy",
        )
        renamed: dict[str, str] = {}
        for column in frame.columns:
            if column.strip().lower() not in renamed.values():
                renamed[column] = column.strip().lower()
        frame = frame.rename(renamed)
        if "county" not in frame.columns and {"jurisdiction", "reporting_level"} <= set(frame.columns):
            frame = frame.filter(pl.col("reporting_level") == "county").rename({"jurisdiction": "county"})
        if not {"county", "office", "candidate", "votes"} <= set(frame.columns):
            continue
        grouped.setdefault((state, year), []).append(
            frame.select(
                office=pl.col("office").fill_null("").str.strip_chars(),
                code=pl.col("county")
                .fill_null("")
                .str.to_lowercase()
                .str.replace_all(r"saint ", "st ")
                .str.replace_all("&", "and")
                .str.replace_all(
                    r"\b(city and county of|county of|county|parish|borough|census area|municipality)\b", ""
                )
                .str.replace_all(r"[^a-z]", "")
                .replace_strict(fips.get(state, {}), default=None),
                candidate=pl.col("candidate").fill_null("").str.strip_chars(),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    for (state, year), frames in grouped.items():
        result = questions(pl.concat(frames), sizes.get(state, 1))
        if result.height:
            found[(state, year)] = ("oe", result)

    frames_2016 = (
        pl.scan_csv(raw.cached(MEDSL, MEDSL_2016).path, infer_schema=False, encoding="utf8-lossy")
        .filter(pl.col("stage").str.to_lowercase() == "gen")
        .with_columns(state=pl.col("state_postal"))
    )
    sources: list[tuple[int, pl.LazyFrame, list[str]]] = [
        (2016, frames_2016, ["county_fips", "jurisdiction", "precinct"])
    ]
    for state, file in MEDSL_2022.items():
        lazy = pl.scan_csv(
            raw.cached(MEDSL, f"{DATAVERSE}{file}?format=original").path,
            infer_schema=False,
            encoding="utf8-lossy",
            truncate_ragged_lines=True,
        )
        columns = set(lazy.collect_schema().names())
        if not {"office", "candidate", "votes", "county_fips"} <= columns:
            print(f"MEDSL 2022 {state}: missing columns, skipped")
            continue
        if "stage" in columns:
            lazy = lazy.filter(pl.col("stage").fill_null("GEN").str.to_uppercase() == "GEN")
        for column in ("mode", "precinct", "jurisdiction_fips"):
            if column not in columns:
                lazy = lazy.with_columns(pl.lit(None, pl.String).alias(column))
        sources.append((2022, lazy.with_columns(state=pl.lit(state)), ["county_fips", "jurisdiction_fips", "precinct"]))
    for year, lazy, precinct in sources:
        frame = (
            lazy.with_columns(
                code=pl.col("county_fips").str.replace(r"\.0$", "").str.zfill(5),
                total=pl.col("mode").fill_null("").str.to_lowercase() == "total",
            )
            .filter(pl.col("total") == pl.col("total").any().over(*precinct, "office", "candidate"))
            .select(
                "state",
                office=pl.col("office").fill_null("").str.strip_chars(),
                code=pl.when(pl.col("code").is_in(list(known))).then("code"),
                candidate=pl.col("candidate").fill_null("").str.strip_chars(),
                votes=pl.col("votes")
                .str.replace_all(r"[,\s]", "")
                .cast(pl.Float64, strict=False)
                .round()
                .cast(pl.Int64),
            )
            .collect()
        )
        for (state,), rows in frame.partition_by("state", as_dict=True).items():
            if state in ARCHIVES or (state, year) in found or state not in sizes:
                continue
            result = questions(rows, sizes[state])
            if result.height:
                found[(state, year)] = ("medsl", result)
    text = json.loads(raw.cached(SOURCE, WIKIPEDIA_2022).path.read_text())["parse"]["wikitext"]
    text = re.sub(r"<ref[^>/]*/>|<ref[^>]*>.*?</ref>", "", text, flags=re.DOTALL)
    text = re.sub(r"\[\[(?:[^|\]]*\|)?([^\]]*)\]\]", r"\1", text)
    with connect() as conn:
        states = {
            name: iso[3:]
            for iso, name in conn.execute(
                """select p.iso_code, n.text from polity p join name n on n.entity_id = p.id and n.is_preferred
                   where p.iso_code ~ '^US-[A-Z]{2}$' and n.lang = 'en'"""
            ).fetchall()
        }
    listed: list[dict[str, str | None]] = []
    for section in re.split(r"^==(?!=)\s*", text, flags=re.MULTILINE)[1:]:
        heading, _, body = section.partition("\n")
        state = states.get(heading.strip(" ="))
        if state is None:
            continue
        for chunk in re.split(r"^\|-\s*$", body, flags=re.MULTILINE):
            cells: list[str] = []
            for line in chunk.split("\n"):
                if line.startswith(("{|", "|}", "!", "|+")):
                    continue
                if line.startswith("|"):
                    cells.append(line[1:].strip())
                elif cells:
                    cells[-1] += " " + line.strip()
            if len(cells) < 4:
                continue
            title = re.search(r"'''(.*?)'''", cells[2])
            listed.append(
                {
                    "state": state,
                    "measure": cells[0],
                    "token": token(cells[0]),
                    "type": cells[1],
                    "title": title[1] if title else None,
                    "result": "Passed"
                    if "passed" in cells[3].lower()
                    else "Failed"
                    if "failed" in cells[3].lower()
                    else None,
                }
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(listed).write_parquet(PARSED / "listed_2022.parquet")
    pl.concat(
        result.with_columns(state=pl.lit(state), year=pl.lit(year), source=pl.lit(source))
        for (state, year), (source, result) in sorted(found.items())
    ).write_parquet(PARSED / "measures.parquet")
    print(sorted((state, year, source) for (state, year), (source, _) in found.items()))


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "measures.parquet")
    listed = pl.read_parquet(PARSED / "listed_2022.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Statewide ballot questions by county, 2016–2022, from OpenElections and MEDSL's precinct returns",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def note(entity: UUID, kind: str, body: str) -> None:
            cs.add("note", entity_id=entity, kind=kind, body_md=body)

        sources: dict[str, UUID] = {"oe": cs.source(OE_PUBLISHER, OE_TITLE)}
        cs.add(
            "source",
            id=sources["oe"],
            kind="community",
            publisher=OE_PUBLISHER,
            title=OE_TITLE,
            url="https://openelections.net",
            license="Public record",
        )
        used = set(frame.filter(pl.col("source") == "oe").select("state", "year").iter_rows())
        for state in COMMITS:
            if any(state == used_state for used_state, _ in used):
                cs.artifact(raw.cached(SOURCE, tree(state)), sources["oe"])
        for state, year, path in picks():
            if (state, year) in used:
                cs.artifact(raw.cached(SOURCE, repository(state, path)), sources["oe"])
        for year, (title, url) in MEDSL_TITLES.items():
            sources[f"medsl{year}"] = cs.source(MEDSL_PUBLISHER, title)
            cs.add(
                "source",
                id=sources[f"medsl{year}"],
                kind="academic",
                publisher=MEDSL_PUBLISHER,
                title=title,
                url=url,
                license="CC0 1.0",
                license_url="https://creativecommons.org/publicdomain/zero/1.0/",
            )
        cs.artifact(raw.cached(MEDSL, MEDSL_2016), sources["medsl2016"])
        medsl_2022 = set(frame.filter(pl.col("source") == "medsl", pl.col("year") == 2022)["state"].unique())
        for state, file in MEDSL_2022.items():
            if state in medsl_2022:
                cs.artifact(raw.cached(MEDSL, f"{DATAVERSE}{file}?format=original"), sources["medsl2022"])
        system = cs.key("us.electoral_system", "referendum")
        names: dict[str, str] = {}
        states: dict[str, tuple[UUID, str]] = {}
        for abbreviation, area, code, text in conn.execute(
            """select substr(p.iso_code, 4), u.area_id, u.code, n.text from reporting_unit u
               join polity_territory t on t.area_id = u.area_id
               join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               where u.scheme_code = 'us.state'"""
        ).fetchall():
            states[abbreviation] = (area, code)
            names[abbreviation] = text
        sizes = dict(
            conn.execute(
                """select left(code, 2), count(*) from reporting_unit where scheme_code = 'us.county'
                   and code <> '51515' and (valid_to is null or valid_to > '2016-01-01') group by 1"""
            ).fetchall()
        )
        wiki: dict[tuple[str, date], list[tuple[str, UUID, UUID, str, str, int, int]]] = {}
        for key, contest, election, state, day, yes_option, no_option, yes, no in conn.execute(
            """select x.value, c.id, c.election_id, substr(p.iso_code, 4), e.date_from,
                      max(o.id::text) filter (where o.ballot_label = 'Yes'),
                      max(o.id::text) filter (where o.ballot_label = 'No'),
                      sum(v.value) filter (where o.ballot_label = 'Yes')::bigint,
                      sum(v.value) filter (where o.ballot_label = 'No')::bigint
               from contest c
               join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                 and x.value like '%%:question:wikipedia:%%'
               join election e on e.id = c.election_id join polity p on p.id = e.polity_id
               join result_set rs on rs.contest_id = c.id and rs.is_default
               join result_value v on v.result_set_k = rs.k
               join contest_option o on o.contest_id = c.id and o.k = v.option_k
               group by 1, 2, 3, 4, 5"""
        ).fetchall():
            wiki.setdefault((state, day), []).append((key, contest, election, yes_option, no_option, yes, no))
        known = {value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'")}
        paths = dict(
            conn.execute(
                """select x.value, s.path from external_id x join slug s on s.entity_id = x.entity_id and s.is_current
                   where x.scheme = 'us.election'"""
            ).fetchall()
        )
        taken = {path for (path,) in conn.execute("select path from slug where path like 'us/%'").fetchall()}
        held = dict(
            conn.execute(
                """select s.entity_id, s.path from slug s join changeset c on c.id = s.changeset_id
                   where s.is_current and c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        skipped: dict[str, int] = {}
        attachments: dict[
            str, list[tuple[int, tuple[str, UUID, UUID, str, str, int, int], pl.DataFrame, UUID, str]]
        ] = {}
        for (state, year, office, origin), rows in frame.group_by(
            "state", "year", "office", "source", maintain_order=True
        ):
            day = DAYS[year]
            label = "OpenElections" if origin == "oe" else "MEDSL"
            source = sources["oe"] if origin == "oe" else sources[f"medsl{year}"]
            yes = int(rows.filter(pl.col("answer") == "YES")["votes"].sum())
            no = int(rows.filter(pl.col("answer") == "NO")["votes"].sum())
            if year < 2022:
                match = [
                    entry
                    for entry in wiki.get((state, day), [])
                    if abs(yes - entry[5]) <= max(50, entry[5] * 0.02)
                    and abs(no - entry[6]) <= max(50, entry[6] * 0.02)
                ]
                if len(match) > 1:
                    match = [
                        entry
                        for entry in match
                        if token(office) is not None
                        and token(entry[0].split(":")[-1].replace("-", " ")) == token(office)
                    ]
                if len(match) != 1:
                    print(f"{state} {year} {office[:60]!r}: {len(match)} statewide matches for {yes} to {no}")
                    skipped["unmatched"] = skipped.get("unmatched", 0) + 1
                    continue
                distance = abs(yes - match[0][5]) + abs(no - match[0][6])
                attachments.setdefault(match[0][0], []).append((distance, match[0], rows, source, label))
                continue
            else:
                if rows["code"].n_unique() < sizes.get(states[state][1], 0):
                    print(
                        f"{state} {year} {office[:60]!r}: {rows['code'].n_unique()} of {sizes[states[state][1]]} counties"
                    )
                    skipped["incomplete"] = skipped.get("incomplete", 0) + 1
                    continue
                code = token(office)
                found = listed.filter(pl.col("state") == state, pl.col("token") == code) if code else listed.clear()
                entry = found.row(0, named=True) if found.height == 1 else None
                election_key = f"{day}:{state}"
                election = cs.key("us.election", election_key)
                if election_key not in known:
                    known.add(election_key)
                    cs.add(
                        "election",
                        id=election,
                        polity_id=cs.polity(f"US-{state}"),
                        parent_id=cs.key("us.election", str(day)) if str(day) in known else None,
                        kind="general",
                        date_from=day,
                        date_to=day,
                        status="held",
                    )
                    name(election, f"{day.year} {names[state]} general election")
                    paths[election_key] = f"us/{state.lower()}/{day}-general"
                    cs.add("slug", path=paths[election_key], entity_id=election)
                if entry is not None and entry["title"]:
                    title = f"{names[state]} {entry['measure']}, {entry['title']}"
                else:
                    words = office.lower().split()
                    title = " ".join(
                        word.upper()
                        if any(character.isdigit() for character in word)
                        else word
                        if index and word in SMALL
                        else word.capitalize()
                        for index, word in enumerate(words)
                    )
                    if len(title) > 80 and ":" in title:
                        title = title.split(":")[0]
                kind = (entry["type"] if entry else "").lower()
                text = f"{office} {entry['title'] if entry else ''}".lower()
                contest_key = f"{day}:{state}:question:county:{slug(office)[:80]}"
                contest = cs.key("us.contest", contest_key)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    district_area_id=states[state][0],
                    electoral_system_id=system,
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add(
                    "ballot_question",
                    contest_id=contest,
                    type="initiative"
                    if "citizen" in kind or "initiat" in text
                    else "optional_referendum"
                    if "veto" in kind
                    else "plebiscite"
                    if "advisory" in kind
                    else "mandatory_referendum",
                )
                name(contest, title)
                if contest in cs.minted:
                    path = held.get(contest)
                    if path is None:
                        base = f"{paths[election_key]}/{slug(title)[:60].rstrip('-') or 'question'}"
                        path, n = base, 1
                        while path in taken:
                            n += 1
                            path = f"{base}-{n}"
                        taken.add(path)
                    cs.add("slug", path=path, entity_id=contest)
                options = {}
                for position, answer in enumerate(("YES", "NO")):
                    printed = rows.filter(pl.col("answer") == answer)["label"].mode().first()
                    options[answer] = cs.key("us.option", f"{contest_key}:{answer.lower()}")
                    cs.add(
                        "contest_option",
                        id=options[answer],
                        contest_id=contest,
                        kind="answer",
                        ballot_position=position + 1,
                        ballot_label=re.split(r"\W", printed.strip())[0].capitalize(),
                    )
                result_set = cs.key(f"{SOURCE}.result_set", contest_key)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="active",
                    source_id=source,
                    is_default=True,
                    completeness=1,
                )
                result = entry["result"] if entry else None
                note(result_set, "methodology", CREATED.format(source=label, fallback="" if result else FALLBACK))
                passed = result == "Passed" if result else yes > no
                cs.add("outcome", result_set_id=result_set, option_id=options["YES" if passed else "NO"], kind="won")
            for code, answer, votes in rows.select("code", "answer", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.county", code),
                    option_id=options.get(answer),
                    channel=0,
                    measure=MEASURES[answer],
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        for key, found in attachments.items():
            if len(found) > 1:
                skipped["duplicate"] = skipped.get("duplicate", 0) + len(found) - 1
            _, (_, contest, election, yes_option, no_option, official_yes, official_no), rows, source, label = min(
                found, key=lambda item: item[0]
            )
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:county")
            yes = int(rows.filter(pl.col("answer") == "YES")["votes"].sum())
            no = int(rows.filter(pl.col("answer") == "NO")["votes"].sum())
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=round((yes + no) / (official_yes + official_no), 4),
            )
            note(result_set, "methodology", ATTACHED.format(source=label))
            answers = {"YES": yes_option, "NO": no_option}
            for code, answer, votes in rows.select("code", "answer", "votes").iter_rows():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=cs.unit("us.county", code),
                    option_id=answers.get(answer),
                    channel=0,
                    measure=MEASURES[answer],
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        print(skipped)
        return cs.write()
