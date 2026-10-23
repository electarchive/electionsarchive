import gzip
import re
from datetime import date, timedelta
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.ks_sos_generals"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{stamp}id_/http://www.kssos.org/elections/"
MIRROR = "https://raw.githubusercontent.com/openelections/openelections-sources-ks/master/"
PUBLISHER = "Kansas Secretary of State"
TITLE = "General election official results by precinct"
LANDING = "https://sos.ks.gov/elections/election-results.html"
FILES = {
    (date(2000, 11, 7), "house"): ("20140113123027", "00elec/2000_general_uscongress_official_results.xls"),
    (date(2000, 11, 7), "president"): ("20140113121029", "00elec/2000_general_president_official_results.xls"),
    (date(2000, 11, 7), "state_house"): ("20140113113414", "00elec/2000_general_kshouse1_65_official_results.xls"),
    (date(2000, 11, 7), "state_house_2"): ("20140113120321", "00elec/2000_general_kshouse66_125_official_results.xls"),
    (date(2000, 11, 7), "state_senate"): ("20140113121857", "00elec/2000_general_kssenate_official_results.xls"),
    (date(2002, 11, 5), "house"): ("20041011092813", "02elec/ELUSHSE02G.xls"),
    (date(2002, 11, 5), "senate"): ("20041011231359", "02elec/ELUSSEN02G.xls"),
    (date(2002, 11, 5), "state_house"): ("20041011134508", "02elec/ELKSREP02G.xls"),
    (date(2002, 11, 5), "governor"): ("20041011135031", "02elec/ELGOV02G.xls"),
    (date(2002, 11, 5), "attorney-general"): ("20041011102011", "02elec/ELAG02G.xls"),
    (date(2002, 11, 5), "secretary-of-state"): ("20041011152120", "02elec/ELSOS02G.xls"),
    (date(2002, 11, 5), "treasurer"): ("20041012003327", "02elec/ELTRES02G.xls"),
    (date(2002, 11, 5), "commissioner-of-insurance"): ("20041011100614", "02elec/ELINS02G.xls"),
    (date(2004, 11, 2), "house"): ("20041217003958", "04elec/ELUSHOU04G.xls"),
    (date(2004, 11, 2), "senate"): ("20041217000909", "04elec/ELUSSEN04G.xls"),
    (date(2004, 11, 2), "president"): ("20041217020322", "04elec/ELPRES04G.xls"),
    (date(2004, 11, 2), "state_house"): ("20041217022455", "04elec/ELKSHOU04G.xls"),
    (date(2004, 11, 2), "state_senate"): ("20041217001407", "04elec/ELKSSEN04G.xls"),
    (date(2006, 11, 7), "house"): ("20070812011214", "06elec/ELUSHOU06G.xls"),
    (date(2006, 11, 7), "state_house"): ("20070722064525", "06elec/ELKSREP06G.xls"),
    (date(2006, 11, 7), "attorney-general"): ("20070722064506", "06elec/ELAG06G.xls"),
    (date(2006, 11, 7), "secretary-of-state"): ("20070812005728", "06elec/ELSOS06G.xls"),
    (date(2006, 11, 7), "treasurer"): ("20070812010958", "06elec/ELTRES06G.xls"),
    (date(2006, 11, 7), "commissioner-of-insurance"): ("20070630095607", "06elec/ELINS06G.xls"),
    (date(2008, 11, 4), "house"): ("20100613051107", "08elec/2008_general_us_house_official_results.xls"),
    (date(2008, 11, 4), "senate"): ("20100613051843", "08elec/2008_general_us_senate_official_results.xls"),
    (date(2008, 11, 4), "president"): ("20100613051348", "08elec/2008_general_president_official_results.xls"),
    (date(2008, 11, 4), "state_house"): ("20100613051305", "08elec/2008_general_ks_house_official_results.xls"),
    (date(2008, 11, 4), "state_senate"): ("20100613051544", "08elec/2008_general_ks_senate_official_results.xls"),
    (date(2010, 11, 2), "house"): ("20111224082840", "10elec/2010_general_us_house_official_results.xls"),
    (date(2010, 11, 2), "senate"): ("20111224081935", "10elec/2010_general_us_senate_official_results.xls"),
    (date(2010, 11, 2), "governor"): ("20111224084852", "10elec/2010_general_governor_official_results.xls"),
    (date(2010, 11, 2), "state_house"): ("20111224085726", "10elec/2010_general_ks_house_official_results.xls"),
    (date(2010, 11, 2), "state_senate"): ("20111224083223", "10elec/2010_general_ks_senate_official_results.xls"),
    (date(2010, 11, 2), "attorney-general"): (
        "20111224085143",
        "10elec/2010_general_attorney_general_official_results.xls",
    ),
    (date(2010, 11, 2), "secretary-of-state"): (
        "20111224074643",
        "10elec/2010_general_secretary_of_state_official_results.xls",
    ),
    (date(2010, 11, 2), "treasurer"): ("20111224080509", "10elec/2010_general_state_treasurer_official_results.xls"),
    (date(2010, 11, 2), "commissioner-of-insurance"): (
        "20111224090351",
        "10elec/2010_general_commissioner_of_insurance_official_results.xls",
    ),
    (date(2012, 11, 6), "state_house"): ("20130920195721", "12elec/2012_general_ks_house_official_results.xlsx"),
    (date(2012, 11, 6), "state_senate"): ("20130920203211", "12elec/2012_general_ks_senate_official_results.xlsx"),
    (date(2014, 11, 4), "state_house"): (None, "2014/2014_general_ks_house_official_results.xls.gz"),
}


SWAPPED = {
    "2008-11-04:KS:house:104:g": {
        'O\'NEAL, MICHAEL R. "MIKE"': "Michael R. Oneal",
        'FERGUSON, BENNIE LEE "BEN"': "Bennie Lee Ferguson",
    },
}
CAVEAT = (
    "Klarner's returns give each candidate the other's party and votes. The Secretary of State's precinct file "
    "and its official results agree with each other, so this set replaces Klarner's as the default."
)
COLUMNS = ["COUNTY", "PRECINCT", "RACE", "DISTRICT", "CANDIDATE", "FIRST", "MIDDLE", "LAST", "SUFFIX", "PARTY", "VOTES"]


def url(key: tuple[date, str]) -> str:
    stamp, name = FILES[key]
    return MIRROR + name if stamp is None else WAYBACK.format(stamp=stamp) + name


def fetch() -> None:
    for key in FILES:
        raw.fetch(SOURCE, url(key), suffix=Path(FILES[key][1]).suffix)


def clean(text: object) -> str:
    return " ".join(str(text or "").split())


def long_rows(path: Path | bytes) -> list[tuple[str, str, str | None, str, str, object]]:
    grid = pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
    head = next(r for r, row in enumerate(grid) if "SUFFIX" in map(clean, row))
    columns = {clean(name): i for i, name in enumerate(grid[head]) if clean(name)} | {
        name: i for i, name in enumerate(COLUMNS) if clean(grid[head][i]) in ("", name)
    }
    return [
        (
            clean(row[columns["COUNTY"]]),
            clean(row[columns["PRECINCT"]]),
            clean(row[columns["DISTRICT"]]) if "DISTRICT" in columns else None,
            clean(row[columns["CANDIDATE"]]),
            clean(row[columns["PARTY"]]),
            row[columns["VOTES"]],
        )
        for row in grid[head + 1 :]
        if row[columns["COUNTY"]] and row[columns["CANDIDATE"]]
    ]


def wide_rows(path: Path) -> list[tuple[str, str, str | None, str, str, object]]:
    grid = pl.read_excel(path, sheet_id=1, has_header=False, infer_schema_length=0).rows()
    out = []
    totals: dict[tuple[str, int], int] = {}
    start = next(i for i, cell in enumerate(grid[1]) if cell is not None)
    county = None
    for row in grid[3:]:
        row = [clean(cell).replace(",", "") or None for cell in row]
        name, vtd, precinct = row[start - 3 : start]
        if name is not None and vtd is None and precinct is None:
            for i in range(start, len(row)):
                if row[i] is not None and grid[0][i] is not None:
                    totals[(name, i)] = int(row[i])
            continue
        precinct = precinct or vtd
        if name is not None:
            county = name
        elif precinct is None or precinct.lower().startswith("district total"):
            continue
        elif precinct.lower().startswith("adv"):
            precinct = "ADVANCE"
        elif precinct.lower().startswith(("prov", "proav")):
            precinct = "PROVISIONAL"
        for i in range(start, len(row)):
            if row[i] is None or grid[0][i] is None:
                continue
            district = re.search(r"District (\d+)", grid[1][i] or "")
            out.append(
                (county, precinct, district[1] if district else None, clean(grid[0][i]), clean(grid[2][i]), row[i])
            )
            totals[(county, -i - 1)] = totals.get((county, -i - 1), 0) + int(row[i])
    for (name, i), value in totals.items():
        if i < 0:
            continue
        summed = totals.get((name, -i - 1), 0)
        if abs(summed - value) > max(10, value * 0.01):
            raise ValueError(f"{path.name}: {name} {grid[0][i]} precincts sum to {summed}, county total {value}")
        if summed != value:
            print(f"{path.name}: {name} {clean(grid[0][i])} precincts sum to {summed}, county total {value}")
    return out


def parse() -> None:
    with connect() as conn:
        counties = {
            re.sub(r"[^a-z]", "", text.lower()): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '20%%'"""
            ).fetchall()
        }
    frames = []
    for key in FILES:
        day, office = key
        path = raw.cached(SOURCE, url(key)).path
        rows = (
            wide_rows(path)
            if day.year == 2000
            else long_rows(gzip.decompress(path.read_bytes()) if path.suffix == ".gz" else path)
        )
        frame = pl.DataFrame(
            rows, schema=["county", "precinct", "district", "candidate", "party", "votes"], orient="row"
        ).with_columns(
            fips=pl.col("county")
            .str.to_lowercase()
            .str.replace(r" county$", "")
            .str.replace_all(r"[^a-z]", "")
            .replace_strict(counties, default=None),
            votes=pl.col("votes")
            .cast(pl.String)
            .str.replace_all(",", "")
            .cast(pl.Float64, strict=False)
            .cast(pl.Int64),
        )
        lost = frame.filter(pl.col("fips").is_null())["county"].unique().to_list()
        if lost:
            raise ValueError(f"{path.name}: unmatched counties {lost[:5]}")
        chamber = office.removesuffix("_2")
        frames.append(
            frame.select(
                year=pl.lit(day.year, pl.Int32),
                chamber=pl.concat_str(
                    pl.lit(chamber.removeprefix("state_")),
                    pl.col("district"),
                    pl.lit("gs" if chamber == "state_senate" and day.year % 4 else "g"),
                    separator=":",
                )
                if chamber.startswith("state_")
                else pl.lit(chamber),
                day=pl.lit(day),
                special=pl.lit(False),
                state=pl.lit("KS"),
                district=pl.col("district").cast(pl.Int16) if chamber == "house" else pl.lit(None, pl.Int16),
                code=pl.concat_str(pl.lit(f"{day.year}:KS:"), "fips", pl.lit("::"), "precinct"),
                fips="fips",
                candidate=pl.col("candidate").str.to_uppercase(),
                writein=pl.col("party").str.to_lowercase().str.contains("write")
                | pl.col("candidate").str.to_uppercase().str.contains("WRITE"),
                votes="votes",
            )
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.concat(frames)
        .filter(pl.col("votes").is_not_null())
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "fips", "candidate")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(measure=pl.lit(1, pl.Int16), kind=pl.lit("precinct"), source=pl.lit("KS"))
        .write_parquet(PARSED / "precincts.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "precincts.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Kansas's federal, statewide, and legislative general elections by precinct, 2000-2014 "
            "(Secretary of State)",
            polity="US-KS",
        )
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=LANDING,
                license="Public record",
            )
        for key in FILES:
            cs.artifact(raw.cached(SOURCE, url(key)), source)
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct x.value from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status = 'active'
                   join changeset g on g.id = r.changeset_id and g.pipeline <> %s
                   join result_value v on v.result_set_k = r.k
                   join reporting_unit u on u.k = v.unit_k and u.scheme_code = 'us.precinct'
                   where x.scheme = 'us.contest' and x.value ~ '^20(0[0-9]|1[0-4])-..-..:KS:'""",
                [SOURCE],
            ).fetchall()
        }
        keyed = frame.with_columns(
            key=pl.concat_str(
                pl.col("day").cast(pl.String),
                pl.lit("KS"),
                pl.when(pl.col("district").is_not_null())
                .then(pl.lit("house:") + pl.col("district").cast(pl.String))
                .otherwise("chamber"),
                separator=":",
            )
        )
        frame = keyed.filter(~pl.col("key").is_in(list(filled))).drop("key")
        medsl.attach(cs, conn, frame.drop("fips"), {"KS": source}, grain="precinct", strict=True)
        for key, labels in SWAPPED.items():
            previous, election, contest, stage, kind, origin, completeness, first = conn.execute(
                """select r.id, r.election_id, r.contest_id, r.stage, r.kind, r.source_id, r.completeness,
                          (select min(v.count_round) from result_value v where v.result_set_k = r.k)
                   from external_id x join result_set r on r.contest_id = x.entity_id
                   where x.scheme = 'us.contest' and x.value = %s and r.source_id <> %s""",
                [key, source],
            ).fetchone()
            options = {
                row[0]: row
                for row in conn.execute(
                    """select ballot_label, id, contest_id, kind, parent_option_id, party_id, alliance_id, ballot_position
                       from contest_option where contest_id = %s""",
                    [contest],
                ).fetchall()
            }
            (a, b) = labels.values()
            for label, party in ((a, options[b][5]), (b, options[a][5])):
                _, id, contest_id, option_kind, parent, _, alliance, position = options[label]
                cs.add(
                    "contest_option",
                    id=id,
                    contest_id=contest_id,
                    kind=option_kind,
                    parent_option_id=parent,
                    party_id=party,
                    alliance_id=alliance,
                    ballot_position=position,
                    ballot_label=label,
                )
            lines = frame.filter(
                pl.concat_str(pl.col("day").cast(pl.String), pl.lit("KS"), "chamber", separator=":") == key
            ).filter(pl.col("candidate").is_in(list(labels)))
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
            cs.add(
                "result_set",
                id=previous,
                election_id=election,
                contest_id=contest,
                stage=stage,
                kind=kind,
                source_id=origin,
                status="superseded",
                is_default=False,
                completeness=completeness,
            )
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
                supersedes_id=previous,
                completeness=1,
            )
            cs.add("note", entity_id=result_set, kind="caveat", body_md=CAVEAT)
            totals = lines.group_by("candidate").agg(pl.col("votes").sum()).sort("votes", descending=True)
            cs.add(
                "outcome",
                result_set_id=result_set,
                option_id=options[labels[totals["candidate"][0]]][1],
                kind="elected",
                seats=1,
            )
            cs.frame(
                "result_value",
                lines.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("code").map_elements(
                        lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String
                    ),
                    option_id=pl.col("candidate").replace_strict(
                        {name: str(options[label][1]) for name, label in labels.items()}
                    ),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(first, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                ),
            )
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        parents = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code like '20%%'"
            ).fetchall()
        }
        units = (
            frame.group_by("code")
            .agg(pl.col("fips").first(), pl.col("day").min())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)) & pl.col("id").is_in([str(unit) for unit in cs.minted]))
        )
        cs.frame(
            "reporting_unit",
            units.select(
                "id",
                polity_id=pl.lit(str(cs.polity("US-KS"))),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("fips").replace_strict({code: str(unit) for code, (unit, _) in parents.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in parents.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{units.height} new precinct units")
        return cs.write()
