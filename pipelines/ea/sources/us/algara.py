import gzip
import re
import struct
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.algara"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
URL = "https://dataverse.harvard.edu/api/access/datafile/5028535"
PUBLISHER = "Algara and Amlani"
TITLE = "Gubernatorial county returns, 1865–2020"
COLUMNS = [
    "election_year",
    "fips",
    "state",
    "democratic_raw_votes",
    "dem_nominee",
    "republican_raw_votes",
    "rep_nominee",
    "raw_county_vote_totals",
]
RENAMED = {"12025": "12086", "46113": "46102", "51515": "51019", "51560": "51005", "51780": "51083"}
MAJORITY = {"VT": 9999, "GA": 9999, "MS": 2019, "ME": 1880, "NH": 1912, "CT": 1901, "RI": 1893, "MA": 1855}
TWO_PARTY = (
    "Algara and Amlani's returns give only the Democratic and Republican nominees' votes and the county's total; "
    "every other candidate's votes are pooled as other votes."
)
SHORT = (
    "In some counties the source's total is below the two major nominees' votes; there the total is taken as their sum, "
    "with no other votes."
)
UNDECIDED = (
    "No winner is recorded: with only the two major parties' votes, the pooled other votes could hold the winner, "
    "or no candidate reached the majority the state required."
)
BLANK = "Counties where the source provides a total but no votes for either major nominee are excluded from this table."


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".rdata")


def objects(path: Path) -> dict[str, object]:
    data = gzip.decompress(path.read_bytes())
    if data[:5] not in (b"RDX2\n", b"RDX3\n"):
        raise ValueError("not an R data file")
    position = 7
    refs: list[object] = []

    def ints(count: int) -> tuple[int, ...]:
        nonlocal position
        values = struct.unpack_from(f">{count}i", data, position)
        position += 4 * count
        return values

    def item() -> object:
        nonlocal position
        (flags,) = ints(1)
        kind = flags & 0xFF
        if kind in (241, 242, 250, 251, 252, 253, 254):
            return None
        if kind == 255:
            return refs[(flags >> 8 or ints(1)[0]) - 1]
        if kind == 1:
            symbol = item()
            refs.append(symbol)
            return symbol
        if kind in (2, 3, 5, 6, 17, 239, 240):
            if flags & 512:
                item()
            tag = item() if flags & 1024 else None
            head, rest = item(), item()
            return [(tag, head), *(rest if isinstance(rest, list) else [])]
        if kind == 9:
            (length,) = ints(1)
            if length == -1:
                return None
            position += length
            return data[position - length : position].decode()
        if kind == 238:
            info, state, _ = item(), item(), item()
            if not isinstance(info, list) or info[0][1] != "compact_intseq" or not isinstance(state, dict):
                raise ValueError("unsupported compact vector")
            count, start, step = state["values"]
            return {"values": [start + index * step for index in range(int(count))], "attributes": {}}
        if kind in (10, 13, 14, 16, 19):
            (length,) = ints(1)
            if kind in (10, 13):
                values: list[object] = [None if value == -(2**31) else value for value in ints(length)]
            elif kind == 14:
                values = list(struct.unpack_from(f">{length}d", data, position))
                position += 8 * length
            else:
                values = [item() for _ in range(length)]
            attributes = item() if flags & 512 else []
            return {"values": values, "attributes": dict(attributes) if isinstance(attributes, list) else {}}
        raise ValueError(f"unsupported R type {kind}")

    (version,) = ints(1)
    ints(2)
    if version == 3:
        position += 4 + ints(1)[0]
    found = item()
    if not isinstance(found, list):
        raise TypeError("no objects")
    return dict(found)


def parse() -> None:
    frame = objects(raw.cached(SOURCE, URL).path)["gov_elections_release"]
    if not isinstance(frame, dict):
        raise TypeError("no gubernatorial returns")
    columns = {
        name: column["values"]
        for name, column in zip(frame["attributes"]["names"]["values"], frame["values"], strict=True)
    }
    with connect() as conn:
        counties = {code for (code,) in conn.execute("select code from reporting_unit where scheme_code = 'us.county'")}

    def person(text: str) -> str | None:
        if text in ("None", ""):
            return None
        return re.sub(r"^([^,]+), (.+)$", r"\2 \1", text)

    rows = (
        pl.DataFrame({name: columns[name] for name in COLUMNS}, strict=False)
        .with_columns(
            year=pl.col("election_year").cast(pl.Int32),
            code=pl.col("fips").replace(RENAMED),
            democratic=pl.col("dem_nominee").fill_null("").map_elements(person, return_dtype=pl.String),
            republican=pl.col("rep_nominee").fill_null("").map_elements(person, return_dtype=pl.String),
            d=pl.col("democratic_raw_votes").fill_nan(None).cast(pl.Int64),
            r=pl.col("republican_raw_votes").fill_nan(None).cast(pl.Int64),
            total=pl.col("raw_county_vote_totals").fill_nan(None).cast(pl.Int64),
        )
        .filter(pl.col("total").is_not_null())
        .with_columns(coarse=(~pl.col("code").is_in(list(counties))).any().over("year", "state"))
        .with_columns(code=pl.when("coarse").then(None).otherwise("code"))
    )
    major = pl.col("d").fill_null(0) + pl.col("r").fill_null(0)
    rows = rows.with_columns(short=major > pl.col("total")).with_columns(total=pl.max_horizontal("total", major))
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        rows.group_by("year", "state", "code")
        .agg(
            pl.col("democratic", "republican").first(),
            pl.col("d").sum(),
            pl.col("r").sum(),
            pl.col("total").sum(),
            pl.col("short").any(),
        )
        .sort("state", "year", "code")
        .write_parquet(PARSED / "governors.parquet")
    )
    coarse = rows.filter("coarse").select("year", "state").unique().height
    print(f"{coarse} of {rows.select('year', 'state').unique().height} races have a county with no current FIPS code")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "governors.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Governors' general elections by county 1865–2020 from Algara and Amlani's returns",
            polity="US",
        )

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://doi.org/10.7910/DVN/DGUMFI",
            license="CC0 1.0",
        )
        cs.artifact(raw.cached(SOURCE, URL), source)
        states: dict[str, tuple[UUID, UUID, str]] = {
            abbreviation: (unit, area, text)
            for abbreviation, unit, area, text in conn.execute(
                """select substr(p.iso_code, 4), u.id, u.area_id, n.text from reporting_unit u
                   join polity_territory t on t.area_id = u.area_id
                   join polity p on p.id = t.polity_id and p.iso_code like 'US-%'
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        units: dict[str, UUID] = dict(
            conn.execute("select code, id from reporting_unit where scheme_code = 'us.county'").fetchall()
        )
        generals: dict[tuple[str, int], list[str]] = {}
        seated: set[str] = set()
        for value, seats in conn.execute(
            """select x.value, exists (select from contest c where c.election_id = x.entity_id and c.kind = 'body_seats')
               from external_id x join election e on e.id = x.entity_id where x.scheme = 'us.election'
                 and split_part(x.value, ':', 1) in (e.date_from::text, extract(year from e.date_from)::text)
                 and not exists (select from entity_merge m where m.entity_id = x.entity_id)"""
        ):
            parts = value.split(":")
            if len(parts) == 2 and len(parts[0]) == 10 and parts[1] in states:
                generals.setdefault((parts[1], int(parts[0][:4])), []).append(parts[0])
                if seats:
                    seated.add(value)
        merged = {entity for entities in cs.merged.values() for entity in entities}
        defaults: dict[UUID, bool] = dict(
            conn.execute(
                """select r.id, r.is_default from result_set r join external_id x on x.entity_id = r.id
                   where x.scheme = %s""",
                [f"{SOURCE}.result_set"],
            ).fetchall()
        )
        own: dict[tuple[str, int], str] = {}
        for (value,) in conn.execute(
            """select x.value from external_id x join contest c on c.id = x.entity_id
               join changeset s on s.id = c.changeset_id
               where x.scheme = 'us.contest' and s.pipeline = %s and x.value ~ '^[0-9-]+:[A-Z]{2}:governor$'""",
            [SOURCE],
        ):
            datekey, state, _ = value.split(":")
            own.setdefault((state, int(datekey[:4])), datekey)
        others: dict[tuple[str, int], list[tuple[str, UUID, list[tuple[UUID, set[str]]]]]] = {}
        for key, contest, option, label in conn.execute(
            """select e.value, c.id, o.id, o.ballot_label from external_id e
               join contest c on c.id = e.entity_id join changeset s on s.id = c.changeset_id
               join election l on l.id = c.election_id and l.kind = 'general'
               left join contest_option o on o.contest_id = c.id and o.parent_option_id is null
               where e.scheme = 'us.contest' and e.value ~ '^\\d{4}-\\d{2}-\\d{2}:[A-Z]{2}:governor$'
                 and s.pipeline <> %s""",
            [SOURCE],
        ).fetchall():
            day, state, _ = key.split(":")
            found = others.setdefault((state, int(day[:4])), [])
            if not found or found[-1][0] != key:
                found.append((key, contest, []))
            if option is not None:
                found[-1][2].append((option, set(slug(label or "").split("-"))))
        fptp = cs.key("us.electoral_system", "fptp")
        two_round = cs.key("us.electoral_system", "two_round")
        parties = {
            "democratic": cs.key("us.party", "democratic-party"),
            "republican": cs.key("us.party", "republican-party"),
        }
        if cs.minted & {fptp, two_round, *parties.values()}:
            raise ValueError("the fptp and two-round systems and the major parties must exist")
        persons: dict[str, UUID] = {}
        offices: set[UUID] = set()

        for (year, state), lines in frame.group_by("year", "state", maintain_order=True):
            if state not in states:
                continue
            unit, area, state_name = states[state]
            found = others.get((state, year), [])
            if len(found) > 1:
                print(f"skipped {year} {state}: {len(found)} governor races on file that year")
                continue
            office = cs.key("us.office", f"governor:{state}")
            if office in cs.minted and office not in offices:
                offices.add(office)
                cs.add("office", id=office, polity_id=cs.polity(f"US-{state}"), kind="governor")
                name(office, f"Governor of {state_name}")
            reused = bool(found)
            days = generals.get((state, year), [])
            if reused:
                contest_key, contest, existing = found[0]
                datekey = contest_key.split(":")[0]
            else:
                november = [day for day in days if day[5:7] == "11"]
                house = sorted(day for day in days if f"{day}:{state}" in seated)
                datekey = (
                    house[0]
                    if house
                    else november[0]
                    if len(november) == 1
                    else days[0]
                    if len(days) == 1
                    else str(year)
                )
                previous = own.get((state, year), datekey)
                if previous != datekey:
                    cs.renamed[f"{datekey}:{state}"] = f"{previous}:{state}"
                    placeholder = cs.key("us.election", f"{previous}:{state}")
                    survivor = cs.key("us.election", f"{datekey}:{state}")
                    if previous == str(year) and placeholder != survivor and str(placeholder) not in merged:
                        cs.merge(placeholder, survivor)
                contest_key = f"{datekey}:{state}:governor"
                contest = cs.key("us.contest", contest_key)
                existing = []
            election_key = f"{datekey}:{state}"
            election = cs.key("us.election", election_key)
            if election in cs.minted:
                generals.setdefault((state, year), []).append(datekey)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{state}"),
                    kind="general",
                    date_from=date(year, 1, 1),
                    date_to=date(year, 12, 31),
                    date_precision="year",
                    status="held",
                )
                name(election, f"{year} {state_name} general election")
                cs.add("slug", path=f"us/{state.lower()}/{year}-general", entity_id=election)
            if not reused:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="office",
                    office_id=office,
                    district_area_id=area,
                    term="full",
                    magnitude=1,
                    round_number=1,
                    electoral_system_id=two_round if state == "GA" and year >= 1968 else fptp,
                    default_result_kind="counted",
                )
                name(contest, f"{year} {state_name} gubernatorial election")
                cs.add("slug", path=f"us/{state.lower()}/{datekey}-general/governor", entity_id=contest)
            blank = (pl.col("d").fill_null(0) == 0) & (pl.col("r").fill_null(0) == 0) & (pl.col("total") > 0)
            first = cs.key(f"{SOURCE}.result_set", contest_key)
            result_set = (
                cs.key(f"{SOURCE}.result_set", f"{contest_key}:2")
                if first not in cs.minted and lines.select(blank.any()).item()
                else first
            )
            if result_set != first:
                cs.add(
                    "result_set",
                    id=first,
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="superseded",
                    source_id=source,
                    is_default=False,
                    supersedes_id=None,
                    completeness=None,
                )
                for option, outcome, count in conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [first]
                ).fetchall():
                    cs.add("outcome", result_set_id=first, option_id=option, kind=outcome, seats=count)
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=defaults.get(result_set, defaults.get(first, not reused)),
                supersedes_id=None if result_set == first else first,
                completeness=None,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=TWO_PARTY)
            if lines["short"].any():
                cs.add("note", entity_id=result_set, kind="caveat", body_md=SHORT)
            if lines.select(blank.any()).item():
                cs.add("note", entity_id=result_set, kind="caveat", body_md=BLANK)
            totals: dict[UUID, int] = {}
            pooled = lines.with_columns(
                other=pl.when(blank)
                .then(None)
                .otherwise(pl.col("total") - pl.col("d").fill_null(0) - pl.col("r").fill_null(0)),
                blank=blank,
            )
            for column, votes in (("democratic", "d"), ("republican", "r"), (None, "other")):
                if column is None:
                    if pooled["other"].sum() == 0:
                        continue
                    option = cs.key("us.option", f"{contest_key}:{SOURCE}:other")
                    cs.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Other votes")
                else:
                    candidate = next(iter(pooled[column].drop_nulls().to_list()), None)
                    surname = slug(str(candidate or "").split(" (")[0]).split("-")[-1]
                    option = next((option for option, words in existing if candidate and surname in words), None)
                    if candidate is None and pooled[votes].fill_null(0).sum() == 0:
                        continue
                    if candidate is None:
                        option = cs.key("us.option", f"{contest_key}:{SOURCE}:{column}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=parties[column],
                            ballot_label=f"{column.title()} nominee",
                        )
                    elif option is None:
                        key = f"{state}:{slug(candidate)}"
                        if key not in persons:
                            persons[key] = cs.key(f"{SOURCE}.person", key)
                            if persons[key] in cs.minted:
                                cs.add("person", id=persons[key])
                                name(persons[key], candidate)
                        option = cs.key("us.option", f"{contest_key}:{persons[key]}")
                        cs.add(
                            "contest_option",
                            id=option,
                            contest_id=contest,
                            kind="candidate",
                            party_id=parties[column],
                            ballot_label=candidate,
                        )
                        cs.add(
                            "candidacy",
                            id=cs.key("us.candidacy", f"{contest_key}:{persons[key]}:head"),
                            person_id=persons[key],
                            option_id=option,
                            role="head",
                            ballot_name=candidate,
                            is_independent=False,
                        )
                    totals[option] = int(pooled[votes].sum())
                for code, value in (
                    pooled.filter(~pl.col("blank")).select("code", votes).iter_rows() if result_set in cs.minted else ()
                ):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=unit if code is None else units[code],
                        option_id=option,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=value or 0,
                    )
            if reused or not totals:
                continue
            total = int(pooled["total"].sum())
            leader = max(totals, key=totals.__getitem__)
            other = total - sum(totals.values())
            if totals[leader] * 2 > total or (totals[leader] > other and year > MAJORITY.get(state, 0)):
                cs.add("outcome", result_set_id=result_set, option_id=leader, kind="elected", seats=1)
            else:
                cs.add("note", entity_id=result_set, kind="caveat", body_md=UNDECIDED)
        return cs.write()
