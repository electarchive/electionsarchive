import io
import json
import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl, medsl_state, oe_precincts, openelections
from ea.sources.us.klarner import LOWER
from ea.store import DATA, connect

SOURCE = "us.oe_legislature"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
COMMITS = {
    "AL": ("ba3999b3a63736d5a09d9b90db2774639bede8aa", (2018,)),
    "AR": ("834ccef3cdcf64c8a381811e4d3ad02ec72ba336", (2022,)),
    "AZ": ("bf14331521f62f74cb17848ab11ca3dfd27f4f82", (2016, 2018, 2022)),
    "CA": ("84c2cbb08a268c683800b513726a1ac7dbf08dac", (2022,)),
    "CT": ("f948d0a1ce36871de4a1d696612ab2a268fd0758", (2018,)),
    "DE": ("a2f49b3c57253914c75d1306ab452d354174bd1f", (2016,)),
    "HI": ("56655e716b7752e2ffe0acb2b85c09e70807c4fa", (2016, 2022)),
    "IA": ("9ec8f4e142b5159f67b6aa447a583b63e249fd12", (2022,)),
    "IN": ("b130b78cd7b55e57e766623772424f60c0e640bd", (2016, 2018, 2020, 2022)),
    "KS": ("11acd83f630a1404cfa507671e3ccf579b2887d8", (2016, 2022)),
    "KY": ("025799258505414d62830ea75fd34b58cb2ee59b", (2016,)),
    "MD": ("c0683efed91d246d2d3d86d6ef599624cd21f58d", (2018,)),
    "ME": ("55ee677d8f0a36564e1a234399d5136441bdafe6", (2016, 2018, 2022)),
    "MI": ("2103114f669c2bb25f19792bf5ca290300381396", (2016, 2018, 2020, 2022)),
    "MS": ("84d07603a7eacefa07cb58e90fcb12ce76fcdcce", (2019, 2023)),
    "ND": ("c25145fbab08f849e6eeec319f516d7916533ea1", (2018,)),
    "NJ": ("81ce9f0b7f554e0bd1655a4083b34a633967f840", (2017,)),
    "NV": ("109e5c7d6be95f15c6bad5194b415ebcdf75d7fc", (2016,)),
    "OH": ("c98f2f7f32c8f1b35bff344ed9b784d0eff31c8d", (2020,)),
    "OR": ("76c478184965392b18afeaf8ab0746e5f401a2e9", (2020,)),
    "RI": ("977565f9d7cf7a32d3a2a454282b5fd291cef291", (2022,)),
    "SC": ("5e05b67e15f3614a20d31144df83248235cba6f2", (2022,)),
    "SD": ("9e01b9aea628e9ae17ada6656b649daf319fd212", (2018,)),
    "TX": ("c7185327aa987ecc23fefdde0ee53f5238f5da70", (2016, 2020, 2022)),
    "UT": ("a023ab4d87c4a93f0913f505c6fcbad1a585db57", (2016, 2018, 2022)),
    "WA": ("5ad56d3900dfe3678b827a6125e9c446fd32628b", (2016,)),
    "WI": ("f344a0ee7effcea4cf835750a3584a7fbb754b6d", (2016, 2022)),
    "WV": ("49d32c279d0e02d740c8af4fcb5b5eee380fbed4", (2022,)),
}
ALIASES = {
    ("UT", "saltlakecity"): "saltlake",
    ("MS", "jeffdavis"): "jeffersondavis",
    ("TX", "hildalgo"): "hidalgo",
    ("IN", "clar"): "clark",
}
TIERS = ["__county", "__town", "", "__precinct"]
OTHER = r"\b(u\.?\s?s\.?|united states|congress\w*|county|city|party|levy|referendum|measure|amendment|board|commission\w*|court|judge|regents?|school|recorder|clerk)\b"


def picks() -> list[tuple[str, int, str]]:
    chosen = []
    for state, (commit, years) in COMMITS.items():
        paths = json.loads(
            raw.cached(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit)).path.read_text()
        )["tree"]
        for year in years:
            found: dict[int, list[str]] = {}
            for entry in paths:
                matched = re.fullmatch(
                    rf"(?:[^/]+/)*{year}1[01]\d\d__{state.lower()}__general(__[a-z_']+?)?(_precinct)?\.csv",
                    entry["path"],
                )
                if entry["type"] != "blob" or matched is None:
                    continue
                suffix = (matched[1] or "") + ("__precinct" if matched[2] else "")
                tier = TIERS.index(suffix) if suffix in TIERS else 4 if suffix.endswith("__precinct") else None
                if tier is not None:
                    found.setdefault(tier, []).append(entry["path"])
            if found:
                chosen.extend((state, year, path) for path in sorted(found[min(found)]))
    return chosen


def fetch() -> None:
    for state, (commit, _) in COMMITS.items():
        raw.fetch(SOURCE, openelections.TREE.format(state=state.lower(), commit=commit), suffix=".json")
    for state, _, path in picks():
        raw.fetch(
            SOURCE,
            openelections.REPOSITORY.format(state=state.lower(), commit=COMMITS[state][0], path=path),
            suffix=".csv",
        )


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
        for state, county, town in conn.execute(
            """select distinct split_part(code, ':', 2), split_part(code, ':', 3), split_part(code, ':', 4)
               from reporting_unit where scheme_code = 'us.precinct' and code ~ '^20(16|18|20):(RI|ME|CT):[0-9]{5}:'"""
        ).fetchall():
            fips[state].setdefault(re.sub(r"[^a-z]", "", town.lower()), county)
    for (state, typo), plain in [*oe_precincts.COUNTIES.items(), *ALIASES.items()]:
        fips[state][typo] = fips[state][plain]
    frames = []
    for state, year, path in picks():
        text = raw.cached(
            SOURCE, openelections.REPOSITORY.format(state=state.lower(), commit=COMMITS[state][0], path=path)
        ).path.read_bytes()
        frame = pl.read_csv(
            io.BytesIO(text.replace(b"\r\n", b"\n").replace(b"\r", b"\n")),
            infer_schema_length=0,
            truncate_ragged_lines=True,
        )
        renamed: dict[str, str] = {}
        for column in frame.columns:
            name = oe_precincts.COLUMNS.get(column.strip().lower(), column.strip().lower())
            name = openelections.ALIASES.get(name, name)
            if name not in renamed.values():
                renamed[column] = name
        frame = frame.rename(renamed)
        if "votes" not in frame.columns and "total" in frame.columns:
            frame = frame.rename({"total": "votes"})
        if "votes" not in frame.columns and {"election_day", "absentee"} & set(frame.columns):
            frame = frame.with_columns(
                votes=pl.sum_horizontal(
                    pl.col(column).str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False)
                    for column in frame.columns
                    if re.fullmatch(r"election_day|early_voting|absentee|mail|provisional|early|in_person", column)
                ).cast(pl.String)
            )
        for column in ("vote_type", "votetype"):
            if column in frame.columns:
                frame = frame.filter(pl.col(column).str.to_lowercase() != "total")
        for first, last in (("first_name", "last_name"), ("first", "last")):
            if "candidate" not in frame.columns and {first, last} <= set(frame.columns):
                frame = frame.with_columns(
                    candidate=pl.concat_str(
                        *(column for column in (first, "middle", last, "suffix") if column in frame.columns),
                        separator=" ",
                        ignore_nulls=True,
                    )
                )
        named = re.search(r"__general__([a-z_']+?)_?_precinct", path)
        if "county" not in frame.columns and named:
            frame = frame.with_columns(county=pl.lit(named[1].replace("_", " ")))
        if "county" not in frame.columns and "town" in frame.columns:
            frame = frame.rename({"town": "county"})
        if (
            "precinct" in frame.columns
            and frame["county"].n_unique() == 1
            and re.sub(r"[^a-z]", "", (frame["county"][0] or "").lower()) not in fips[state]
        ):
            frame = frame.with_columns(county="precinct")
        if not {"county", "office", "candidate", "votes"} <= set(frame.columns):
            print(f"{path}: columns {frame.columns}, skipped")
            continue
        if "precinct" in frame.columns:
            frame = frame.filter(~pl.col("precinct").fill_null("").str.contains(oe_precincts.TOTALS))
        if state == "ME" and year == 2016:
            frame = (
                frame.filter(
                    pl.col("precinct") != "STATE UOCAVA",
                    pl.col("precinct").str.to_uppercase() != pl.col("county").str.to_uppercase(),
                )
                .with_columns(
                    pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Int64, strict=False).cast(pl.String)
                )
                .sort(pl.col("votes").cast(pl.Int64), nulls_last=False)
                .unique(["county", "precinct", "office", "district", "candidate"], keep="last")
            )
        frames.append(
            frame.select(
                state=pl.lit(state),
                year=pl.lit(year),
                day=pl.lit(path).str.extract(r"(\d{8})__").str.to_date("%Y%m%d"),
                county=pl.col("county").fill_null("").str.strip_chars(),
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
                office=pl.col("office").fill_null("").str.strip_chars(),
                district=(pl.col("district") if "district" in frame.columns else pl.lit(None, pl.String))
                .fill_null("")
                .str.strip_chars(),
                candidate=pl.col("candidate").fill_null("").str.strip_chars().str.to_uppercase(),
                votes=pl.col("votes").str.replace_all(r"[,\s]", "").cast(pl.Float64, strict=False).cast(pl.Int64),
            )
        )
    frame = pl.concat(frames).filter(
        pl.col("votes").is_not_null(),
        ~pl.col("candidate").str.contains(oe_precincts.TALLIES),
        ~pl.col("candidate").str.contains(openelections.NOT_COUNTED),
    )
    chambers = {}
    for state, office, district in frame.select("state", "office", "district").unique().iter_rows():
        text = re.sub(r"\s+", " ", office.lower()).strip()
        upper = re.search(r"\bsenat", text)
        statehood = re.search(r"\bstate\b|assembly|delegate|legislat", text) or re.fullmatch(
            r"(state )?(rep|sen)\w* \d+.*", text
        )
        if (
            re.search(OTHER, text)
            or re.search(r"special|unexpired|vacancy", f"{text} {district}".lower())
            or not (
                (upper and (statehood or district))
                or (statehood and re.search(r"\b(house|rep\w*|assembly|delegates?|legislature)\b", text))
            )
        ):
            continue
        body = (
            "legislature"
            if state == "NE"
            else "senate"
            if upper
            else re.sub(
                r"[^a-z]+",
                "-",
                LOWER.get(state, "House of Representatives")
                .removeprefix("State ")
                .removesuffix(" of Representatives")
                .lower(),
            )
        )
        seat = district.lower()
        if not seat:
            found = re.search(r"(\d+(?:\s*-+\s*\d|[a-z]\b)?)", text)
            seat = found[1] if found else ""
        seat = re.sub(r"\.0$", "", seat)
        seat = re.sub(r"^(\d+)(st|nd|rd|th) (representative|senatorial) district$", r"\1", seat)
        seat = re.sub(r"^(ad|sd|hd|d)0*(?=\d)", "", seat)
        post = None
        if found_post := re.fullmatch(r"0*(\d+)\s*-+\s*(\d)", seat):
            seat, post = found_post[1], found_post[2]
        elif found_post := re.search(r"\bpos(?:ition|\.)?\s*(\d)", text):
            post = found_post[1]
        if seat:
            chambers[(state, office, district)] = ":".join(
                [body, medsl_state._district(state, seat)[0], *([post] if post else []), "g"]
            )
    seated = pl.DataFrame(
        [(*key, chamber) for key, chamber in chambers.items()],
        schema={"state": pl.String, "office": pl.String, "district": pl.String, "chamber": pl.String},
        orient="row",
    )
    frame = frame.join(seated, on=["state", "office", "district"])
    lost = frame.filter(pl.col("code").is_null())
    if lost.height:
        print(
            lost.group_by("state", "year", "county").agg(pl.col("votes").sum()).sort("votes", descending=True).head(40)
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        frame.filter(pl.col("code").is_not_null())
        .with_columns(
            special=pl.lit(False),
            district=pl.lit(None, pl.Int16),
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(kind=pl.lit("county"), source="state")
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative general elections by county 2016–2023 from OpenElections' state files, "
            "summed from precincts where no county file exists",
            polity="US",
        )
        covered = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from result_set r
                   join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
                   where r.status = 'active' and x.value ~ '^20(1[5-9]|2[0-5])-'
                     and exists (select from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = r.k
                                   and u.scheme_code in ('us.county', 'us.county_part', 'us.town', 'us.precinct'))"""
            ).fetchall()
        }
        frame = frame.filter(
            ~pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":").is_in(list(covered))
        )
        sources = {}
        for state in frame["state"].unique().sort():
            title = f"{state} precinct-level results"
            sources[state] = cs.source("OpenElections", title)
            if sources[state] in cs.minted:
                cs.add(
                    "source",
                    id=sources[state],
                    kind="community",
                    publisher="OpenElections",
                    title=title,
                    url=f"https://github.com/openelections/openelections-data-{state.lower()}",
                    license=None,
                )
        for state, _, path in picks():
            if state in sources:
                cs.artifact(
                    raw.cached(
                        SOURCE,
                        openelections.REPOSITORY.format(state=state.lower(), commit=COMMITS[state][0], path=path),
                    ),
                    sources[state],
                )
        medsl.attach(cs, conn, frame, sources)
        return cs.write()
