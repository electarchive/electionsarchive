import re
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us.algara import PUBLISHER, RENAMED, TWO_PARTY, objects
from ea.store import DATA, connect

SOURCE = "us.algara_federal"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
FILES = {
    "president": (
        "https://dataverse.harvard.edu/api/access/datafile/5028532",
        "pres_elections_release",
        "Presidential county returns, 1868–2020",
    ),
    "senate": (
        "https://dataverse.harvard.edu/api/access/datafile/5028534",
        "senate_elections_release",
        "US Senate county returns, 1908–2020",
    ),
    "governor": (
        "https://dataverse.harvard.edu/api/access/datafile/5028535",
        "gov_elections_release",
        "Gubernatorial county returns, 1865–2020",
    ),
}
FORMER = {
    ("VA", "NANSEMOND"): ("nansemond", "Nansemond", "51800", date(1974, 1, 1)),
    ("VA", "PRINCESS ANNE"): ("princessanne", "Princess Anne", "51810", date(1963, 1, 1)),
    ("VA", "WARWICK"): ("warwick", "Warwick", "51700", date(1958, 7, 16)),
    ("VA", "SOUTH NORFOLK CITY"): ("southnorfolk", "South Norfolk", "51550", date(1963, 1, 1)),
    ("VA", "ELIZABETH CITY"): ("elizabethcity", "Elizabeth City", "51650", date(1952, 7, 1)),
    ("VA", "NORFOLK"): ("norfolkcounty", "Norfolk County", "51550", date(1963, 1, 1)),
    ("VA", "MANCHESTER"): ("manchester", "Manchester", "51760", date(1910, 4, 1)),
    ("VA", "NORTH DANVILLE"): ("northdanville", "North Danville", "51590", date(1896, 2, 17)),
    ("SD", "WASHABAUGH"): ("washabaugh", "Washabaugh", "46071", date(1983, 1, 1)),
    ("SD", "ARMSTRONG"): ("armstrong", "Armstrong", "46041", date(1952, 11, 5)),
    ("SD", "WASHINGTON"): ("washington", "Washington", "46102", date(1943, 1, 1)),
    ("NV", "ORMSBY"): ("ormsby", "Ormsby", "32510", date(1969, 7, 1)),
    ("GA", "CAMPBELL"): ("campbell", "Campbell", "13121", date(1932, 1, 1)),
    ("GA", "MILTON"): ("milton", "Milton", "13121", date(1932, 1, 1)),
    ("TN", "JAMES"): ("james", "James", "47065", date(1919, 1, 1)),
    ("MI", "MANITOU"): ("manitou", "Manitou", "26089", date(1895, 1, 1)),
    ("MI", "ISLE ROYALE"): ("isleroyale", "Isle Royale", "26083", date(1897, 1, 1)),
    ("MN", "MONONGALIA"): ("monongalia", "Monongalia", "27067", date(1870, 11, 30)),
    ("KS", "GARFIELD"): ("garfield", "Garfield", "20055", date(1893, 2, 15)),
    ("ID", "LOGAN"): ("logan", "Logan", "16013", date(1895, 3, 5)),
    ("ID", "ALTURAS"): ("alturas", "Alturas", "16013", date(1895, 3, 5)),
}
LICENSE = (
    "CC0 1.0 as deposited, but compiled from CQ Press and ICPSR's United States Historical Election Returns, "
    "so held under the ICPSR terms of use"
)
WITHDRAWN = {"1936-11-03:FL:senate:special"}
WITHDRAWN_NOTE = (
    "The county file adds the votes of Florida's two same-day Senate races of 1936 into one line per county, so its "
    "figures belong to neither contest alone. We withdrew this set."
)
PROVENANCE = (
    "Algara and Amlani's returns are reported as compiled from CQ Press and ICPSR's United States Historical "
    "Election Returns, so these county figures are kept beside the official total, never as the default, "
    "and are not redistributed."
)
COUNTY = ("us.county", "us.town", "us.county_part", "us.former_county", "us.ak_house_district", "us.place")


def fetch() -> None:
    for url, _, _ in FILES.values():
        raw.fetch(SOURCE, url, suffix=".rdata")


def parse() -> None:
    with connect() as conn:
        counties = {code for (code,) in conn.execute("select code from reporting_unit where scheme_code = 'us.county'")}
    former = pl.DataFrame(
        [(state, name, f"{state}:{plain}") for (state, name), (plain, _, _, _) in FORMER.items()],
        schema=["state", "county_name", "former"],
        orient="row",
    )
    current = list(counties)
    counties |= set(former["former"])
    frames: list[pl.DataFrame] = []
    for office, (url, name, _) in FILES.items():
        frame = objects(raw.cached(SOURCE, url).path)[name]
        if not isinstance(frame, dict):
            raise TypeError(f"no {office} returns")
        columns = {
            column: values["values"]
            for column, values in zip(frame["attributes"]["names"]["values"], frame["values"], strict=True)
        }
        if "seat_class" not in columns:
            columns["seat_class"] = [None] * len(columns["fips"])
        frames.append(
            pl.DataFrame(
                {
                    column: columns[column]
                    for column in (
                        "election_year",
                        "state",
                        "fips",
                        "county_name",
                        "seat_class",
                        "election_type",
                        "dem_nominee",
                        "rep_nominee",
                        "democratic_raw_votes",
                        "republican_raw_votes",
                        "raw_county_vote_totals",
                    )
                },
                strict=False,
            ).with_columns(office=pl.lit(office))
        )
    rows = (
        pl.concat(frames, how="vertical_relaxed")
        .join(former, on=["state", "county_name"], how="left")
        .with_columns(
            year=pl.col("election_year").cast(pl.Int32),
            code=pl.when(pl.col("fips").replace(RENAMED).is_in(current))
            .then(pl.col("fips").replace(RENAMED))
            .otherwise(pl.coalesce("former", pl.col("fips").replace(RENAMED))),
            special=pl.col("election_type") == "S",
            d=pl.col("democratic_raw_votes").fill_nan(None).cast(pl.Int64).fill_null(0),
            r=pl.col("republican_raw_votes").fill_nan(None).cast(pl.Int64).fill_null(0),
            total=pl.col("raw_county_vote_totals").fill_nan(None).cast(pl.Int64),
        )
        .filter(pl.col("total").is_not_null() | (pl.col("d") + pl.col("r") > 0))
        .with_columns(total=pl.max_horizontal(pl.col("total").fill_null(0), pl.col("d") + pl.col("r")))
        .with_columns(
            coarse=(~pl.col("code").is_in(list(counties)) | pl.col("code").is_null())
            .any()
            .over("office", "year", "state", "seat_class", "special")
        )
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        rows.group_by("office", "year", "state", "seat_class", "special", "code")
        .agg(pl.col("dem_nominee", "rep_nominee", "coarse").first(), pl.col("d", "r", "total").sum())
        .sort("office", "state", "year", "seat_class", "code")
        .write_parquet(PARSED / "returns.parquet")
    )
    print(
        rows.filter("coarse").select("office", "year", "state", "seat_class").unique().height,
        "races with an unmatched county",
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "returns.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="President, US Senate, and governors by county from Algara and Amlani's returns, where no county results exist",
            polity="US",
        )
        sources: dict[str, UUID] = {}
        for office, (url, _, title) in FILES.items():
            sources[office] = cs.source(PUBLISHER, title)
            if office == "governor":
                continue
            cs.add(
                "source",
                id=sources[office],
                kind="academic",
                publisher=PUBLISHER,
                title=title,
                url="https://doi.org/10.7910/DVN/DGUMFI",
                license=LICENSE,
            )
            cs.artifact(raw.cached(SOURCE, url), sources[office])
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        units: dict[str, str] = {code: str(unit) for code, (unit, _) in counties.items()}
        for (state, _), (plain, label, successor, ended) in FORMER.items():
            code = f"{state}:{plain}"
            unit = cs.unit("us.former_county", code)
            units[code] = str(unit)
            if unit in cs.minted and code in set(frame["code"].drop_nulls()):
                cs.add(
                    "reporting_unit",
                    id=unit,
                    polity_id=cs.polity(f"US-{state}"),
                    scheme_code="us.former_county",
                    code=code,
                    parent_id=cs.unit("us.state", successor[:2]),
                    area_relation="within",
                    area_id=counties[successor][1],
                    kind="standard",
                    valid_from=None,
                    valid_to=ended,
                )
                cs.add("name", entity_id=unit, text=label, lang="en", kind="official", is_preferred=True)
        dated: dict[tuple[str, int, str, str], list[tuple[str, UUID]]] = {}
        for key, contest in conn.execute(
            r"""select value, entity_id from external_id where scheme = 'us.contest'
                and value ~ '^\d{4}(-\d{2}-\d{2})?:[A-Z]{2}:(president|senate|senate:special|governor)$'"""
        ).fetchall():
            day, state, rest = key.split(":", 2)
            office = "senate" if rest.startswith("senate") else rest
            dated.setdefault((office, int(day[:4]), state, rest), []).append((key, contest))
        candidates = [contest for found in dated.values() for _, contest in found]
        counted_already = {
            contest
            for (contest,) in conn.execute(
                """select distinct rs.contest_id from result_set rs
                   join result_value v on v.result_set_k = rs.k
                   join reporting_unit u on u.k = v.unit_k
                   where rs.contest_id = any(%s) and rs.status <> 'withdrawn' and u.scheme_code = any(%s)""",
                [candidates, list(COUNTY)],
            ).fetchall()
        }
        published: dict[str, UUID] = dict(
            conn.execute(
                "select value, entity_id from external_id where scheme = %s", [f"{SOURCE}.result_set"]
            ).fetchall()
        )
        races: dict[tuple[str, int, str, bool], list[pl.DataFrame]] = {}
        for (office, year, state, _, special), lines in frame.group_by(
            "office", "year", "state", "seat_class", "special", maintain_order=True
        ):
            races.setdefault((str(office), int(year), str(state), bool(special)), []).append(lines)
        skipped: dict[str, int] = {}

        def skip(reason: str, key: str, detail: str = "") -> None:
            skipped[reason] = skipped.get(reason, 0) + 1
            if reason not in ("already by county", "no contest"):
                print(f"{key}: {detail or reason}")

        values: list[pl.DataFrame] = []
        for (office, year, state, special), groups in races.items():
            rest = "senate:special" if office == "senate" and special else office
            found = dated.get((office, year, state, rest), [])
            if len(found) != 1 or len(groups) != 1:
                skip("no contest" if not found else "ambiguous", f"{year}:{state}:{rest}")
                continue
            key, contest = found[0]
            lines = groups[0]
            if key in WITHDRAWN:
                election, completeness = conn.execute(
                    "select election_id, completeness from result_set where id = %s", [published[key]]
                ).fetchone()
                cs.add(
                    "result_set",
                    id=published[key],
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="withdrawn",
                    source_id=sources[office],
                    is_default=False,
                    completeness=completeness,
                )
                cs.add("note", entity_id=published[key], kind="caveat", body_md=WITHDRAWN_NOTE)
                continue
            if contest in counted_already:
                if key in published:
                    cs.add("note", entity_id=published[key], kind="caveat", body_md=PROVENANCE)
                skip("already by county", key)
                continue
            if lines["coarse"].any():
                skip("a county with no current FIPS code", key)
                continue
            row = conn.execute(
                """select c.election_id, min(v.count_round),
                          coalesce(sum(v.value) filter (where v.measure = 4 and v.option_k = 0),
                                   sum(v.value) filter (where v.measure = 1))::bigint
                   from contest c join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure in (1, 4)
                   where c.id = %s group by 1""",
                [contest],
            ).fetchone()
            if row is None or row[1] is None or row[1] > 1 or not row[2]:
                skip("no official total", key)
                continue
            election, first, official = row
            known = conn.execute(
                """select o.id, lower(coalesce(n.text, '')), lower(coalesce(o.ballot_label, '')),
                          (select sum(v.value)::bigint from result_value v
                           where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1 and v.count_round = %s)
                   from contest_option o
                   join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                   left join name n on n.entity_id = o.party_id and n.is_preferred and n.lang = 'en'
                   where o.contest_id = %s and o.parent_option_id is null and o.kind in ('candidate', 'ticket')""",
                [first, contest],
            ).fetchall()
            known = [line for line in known if line[3]]
            picked: dict[str, str] = {}
            problem: tuple[str, str] | None = None
            for column, votes, words in (
                ("dem_nominee", "d", ("democratic", "farmer")),
                ("rep_nominee", "r", ("republican",)),
            ):
                total = int(lines[votes].sum())
                if total == 0:
                    continue
                nominee = str(lines[column].drop_nulls().first() or "")
                surname = (
                    re.sub(r"[^a-z]", "", re.sub(r"\s+(jr|sr|ii|iii)\.?$", "", nominee.lower()).split(" ")[-1])
                    if nominee
                    else ""
                )
                match = [line for line in known if any(word in line[1] for word in words)]
                if len(match) != 1 and surname:
                    match = [line for line in known if surname in re.sub(r"[^a-z ]", "", line[2]).split()]
                if len(match) != 1:
                    problem = ("no single option", f"no single option for {nominee or votes}")
                    break
                option, _, _, expected = match[0]
                if abs(total - expected) > max(50, expected * 0.02):
                    problem = ("drifted", f"{nominee or votes} has {total} against {expected} on file")
                    break
                picked[votes] = str(option)
            counted = int(lines["total"].sum())
            if problem is None and counted > official * 1.02:
                problem = ("overcounted", f"the county total {counted} exceeds the official {official}")
            if problem is None and counted < official * 0.9:
                problem = ("undercounted", f"the county total {counted} is below 90 percent of the official {official}")
            if problem is not None:
                skip(problem[0], key, problem[1])
                continue
            result_set = cs.key(f"{SOURCE}.result_set", key)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[office],
                is_default=False,
                completeness=round(counted / official, 4),
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=TWO_PARTY)
            cs.add("note", entity_id=result_set, kind="caveat", body_md=PROVENANCE)
            pooled = lines.with_columns(other=pl.col("total") - pl.col("d") - pl.col("r"))
            if pooled["other"].sum() > 0:
                others = cs.key("us.option", f"{key}:{SOURCE}:other")
                cs.add("contest_option", id=others, contest_id=contest, kind="others", ballot_label="Other votes")
                picked["other"] = str(others)
            for votes, option in picked.items():
                values.append(
                    pooled.select(
                        result_set_id=pl.lit(str(result_set)),
                        unit_id=pl.col("code").replace_strict(units),
                        option_id=pl.lit(option),
                        channel=pl.lit(0, pl.Int16),
                        measure=pl.lit(1, pl.Int16),
                        count_round=pl.lit(first, pl.Int16),
                        flags=pl.lit(0, pl.Int16),
                        value=pl.col(votes),
                    )
                )
        print(skipped, len({frame["result_set_id"][0] for frame in values}), "contests filled")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
