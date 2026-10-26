import json
import re
import time
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import medsl_state, openelections
from ea.sources.us.klarner import LOWER
from ea.store import DATA, connect

SOURCE = "us.heda"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DATASET = "https://dataverse.harvard.edu/api/datasets/:persistentId/?persistentId=doi:10.7910/DVN/YN4TLR"
FILE = "https://dataverse.harvard.edu/api/access/datafile/{id}"
TITLE = "Precinct-Level Election Data"
PUBLISHER = "Harvard Election Data Archive"
VOTES = re.compile(r"g(\d{4})_([A-Z]+)_([drt])v(\d*)")
AT_LARGE = {"AK", "DE", "MT", "ND", "SD", "VT", "WY"}
DISTRICTS = {"STH": "ld", "STS": "sd", "USH": "cd"}
OFFICES = {
    "USP": ("president",),
    "USS": ("senate",),
    "GOV": ("governor",),
    "LTG": ("lieutenant-governor",),
    "ATG": ("attorney-general",),
    "SOS": ("secretary-of-state", "secretary-of-the-state", "secretary-of-the-commonwealth"),
    "TRE": ("treasurer",),
    "AUD": ("auditor",),
    "COM": ("comptroller", "controller"),
    "AGR": ("commissioner-of-agriculture", "commissioner-of-agriculture-and-forestry"),
    "INS": ("commissioner-of-insurance",),
    "LND": ("commissioner-of-public-lands",),
    "SPI": ("superintendent-of-public-instruction",),
    "CFO": ("chief-financial-officer",),
}
COUNTY = ("county_name", "county", "parish")
ALPHABETICAL = {"MI", "MN", "PA"}
TYPOS = {
    ("FL", "dad"): "miamidade",
    ("FL", "mrt"): "martin",
    ("FL", "mrn"): "marion",
    ("FL", "clm"): "columbia",
    ("FL", "cll"): "collier",
    ("GA", "augustarichmond"): "richmond",
    ("GA", "mongtomery"): "montgomery",
    ("GA", "meriweather"): "meriwether",
    ("GA", "chatooga"): "chattooga",
    ("GA", "cowetta"): "coweta",
    ("MO", "kansascity"): "jackson",
    ("MT", "choteau"): "chouteau",
    ("MT", "prarie"): "prairie",
    ("MT", "phillps"): "phillips",
    ("KY", "pendelton"): "pendleton",
    ("OR", "clastop"): "clatsop",
    ("SC", "oragneburg"): "orangeburg",
    ("SC", "oragenburg"): "orangeburg",
    ("SC", "sumpter"): "sumter",
    ("SC", "dillion"): "dillon",
    ("SC", "malrboro"): "marlboro",
    ("SC", "bearfort"): "beaufort",
    ("SC", "chsterfield"): "chesterfield",
    ("WY", "uinita"): "uinta",
    ("WY", "freemont"): "fremont",
    ("VA", "bedfordcity"): "bedford",
}
LABELS = ("precinct", "precinct_name", "svprec", "ed_precinct", "ward_precinct", "vtd", "cntyvtd", "precinct_code")
PLACES = ("town", "town_name", "mcd")
TOLERANCE = 0.005
PARTIAL = (
    "The Harvard Election Data Archive records only Democratic and Republican votes by precinct; therefore, the other "
    "candidates in this contest ({others}) have no precinct results here."
)


def files() -> dict[str, int]:
    version = json.loads(raw.cached(SOURCE, DATASET).path.read_text())["data"]["latestVersion"]
    return {
        entry["dataFile"]["filename"]: entry["dataFile"]["id"]
        for entry in version["files"]
        if re.fullmatch(r"[A-Z]{2}_\d{4}\.tab|heda_docs\.pdf", entry["dataFile"]["filename"])
    }


def fetch() -> None:
    raw.fetch(SOURCE, DATASET, suffix=".json")
    for name, file in files().items():
        try:
            raw.cached(SOURCE, FILE.format(id=file))
        except LookupError:
            raw.fetch(SOURCE, FILE.format(id=file), suffix=".pdf" if name.endswith(".pdf") else ".tab")
            time.sleep(3)


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)
    frames = []
    for name, file in sorted(files().items()):
        if not name.endswith(".tab") or int(name[3:7]) < 2000:
            continue
        state = name[:2]
        frame = pl.read_csv(raw.cached(SOURCE, FILE.format(id=file)).path, separator="\t", infer_schema=False)
        wanted = [
            (column, *found.groups())
            for column in frame.columns
            if (found := VOTES.fullmatch(column)) and (found[2] in DISTRICTS or found[2] in OFFICES)
        ]
        if not wanted:
            continue
        named = [
            column for column in COUNTY if column in frame.columns and not frame[column].str.contains(r"^\d+$").all()
        ]
        numbered = ["county"] if state in ALPHABETICAL and "county" in frame.columns else []
        label = next((column for column in LABELS if column in frame.columns), None)
        place = [column for column in (*PLACES, "ward") if column in frame.columns and column != label]
        if not (named or numbered) or not (label or place):
            print(f"{name}: no county or precinct column, skipped")
            continue
        county = (named or numbered)[0]
        names = fips[state]

        codes: dict[str, str | None] = {}
        known = set(names.values())
        for text in frame[county].drop_nulls().unique():
            plain = re.sub(
                r"county|parish|cityandcountyof",
                "",
                re.sub(r"[^a-z]", "", re.sub(r"\bco\.(\s+\d+)?$", "", text.lower().replace("&", "and"))),
            )
            plain = TYPOS.get((state, plain), plain)
            prefixed = {known for key, known in names.items() if len(plain) >= 3 and key.startswith(plain)}
            codes[text] = (
                next(iter(names.values()))[:2] + str(int(text) * 2 - 1).zfill(3)
                if not named
                else names.get(plain) or (prefixed.pop() if len(prefixed) == 1 else None)
            )
            codes[text] = codes[text] if codes[text] in known else None
        frame = frame.filter(
            ~pl.any_horizontal(
                pl.col(column).fill_null("").str.contains(r"(?i)\btotals?\b")
                for column in (county, *place, *([label] if label else []))
            )
        ).with_columns(
            fips=pl.col(county).replace_strict(codes, default=None),
            precinct=pl.concat_str(*place, *([label] if label else []), separator=" ", ignore_nulls=True)
            .str.strip_chars()
            .str.replace_all(r"\s+", " "),
        )
        if frame.select("fips", "precinct").is_duplicated().any():
            extra = [
                column
                for column in frame.columns
                if column not in (county, label, *place, "state", "year", "fips", "precinct")
                and not re.fullmatch(
                    r"[gsrp]\d{4}_.*|(cd|sd|ld|ed|sboed|sbd|ccad)\d*|.*_name|ld_code|cd2010|jud_.*", column
                )
            ]
            unique = next(
                (column for column in extra if not frame.select("fips", "precinct", column).is_duplicated().any()), None
            )
            if unique is not None:
                frame = frame.with_columns(
                    precinct=pl.concat_str("precinct", pl.lit(" "), pl.col(unique).fill_null(""))
                )
        lost = frame.filter(pl.col("fips").is_null())
        if lost.height:
            print(f"{name}: {lost.height} rows in unknown counties {lost[county].unique().to_list()[:8]}, skipped")
        duplicated = frame.select("fips", "precinct").is_duplicated().sum()
        if duplicated:
            print(f"{name}: {duplicated} rows share a county and precinct name")
        for column, year, office, party, split in wanted:
            district = DISTRICTS.get(office)
            seat = f"{district}{split if split not in ('', '1') else ''}" if district else None
            if office == "STH" and "ld_name" in frame.columns and split in ("", "1"):
                seat = "ld_name"
            at_large = office == "USH" and seat not in frame.columns and state in AT_LARGE
            if seat is not None and seat not in frame.columns and not at_large:
                continue
            frames.append(
                frame.filter(pl.col("fips").is_not_null()).select(
                    state=pl.lit(state),
                    year=pl.lit(int(year)),
                    file=pl.lit(name),
                    office=pl.lit(office),
                    district=pl.lit("0")
                    if at_large
                    else pl.col(seat).str.strip_chars()
                    if seat
                    else pl.lit(None, pl.String),
                    fips="fips",
                    precinct="precinct",
                    party=pl.lit(party),
                    votes=pl.col(column).cast(pl.Float64, strict=False).cast(pl.Int64),
                )
            )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.concat(frames)
        .filter(
            pl.col("votes").is_not_null(), (pl.col("district").is_not_null()) | pl.col("office").is_in(list(OFFICES))
        )
        .group_by("state", "year", "file", "office", "district", "fips", "precinct", "party")
        .agg(pl.col("votes").sum())
        .write_parquet(PARSED / "lines.parquet")
    )


def emit() -> Path:
    lines = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Federal, state legislative, and statewide general elections by precinct 2000–2012, Democratic and "
            "Republican votes, from the Harvard Election Data Archive",
            polity="US",
        )
        keys: dict[tuple[str, int, str], str] = {}
        for (value,) in conn.execute(
            "select value from external_id where scheme = 'us.contest' and value ~ '^20(0[0-9]|1[0-2])-1[01]-\\d\\d:'"
        ).fetchall():
            parts = value.split(":", 2)
            if len(parts) == 3:
                keys.setdefault((parts[1], int(parts[0][:4]), parts[2]), value)
        filled = {
            key
            for (key,) in conn.execute(
                """select distinct cx.value from external_id cx join result_set rs on rs.contest_id = cx.entity_id
                   where cx.scheme = 'us.contest' and cx.value ~ '^20(0[0-9]|1[0-2])-1[01]-'
                     and rs.changeset_id not in (select id from changeset where pipeline = %s)
                     and exists (select 1 from result_value v join reporting_unit u on u.k = v.unit_k
                                 where v.result_set_k = rs.k and u.scheme_code = 'us.precinct')""",
                [SOURCE],
            ).fetchall()
        }
        seats: dict[tuple[str, int, str, str | None], str] = {}
        for state, year, office, district in lines.select("state", "year", "office", "district").unique().iter_rows():
            if office in OFFICES:
                tried = [f"{name}{suffix}" for name in OFFICES[office] for suffix in ("", ":special")]
            elif office == "USH":
                number = re.sub(r"\.0$", "", district or "")
                tried = [f"house:{int(number)}"] if number.isdigit() else []
            else:
                body = (
                    "legislature"
                    if state == "NE"
                    else "senate"
                    if office == "STS"
                    else re.sub(
                        r"[^a-z]+",
                        "-",
                        LOWER.get(state, "House of Representatives")
                        .removeprefix("State ")
                        .removesuffix(" of Representatives")
                        .lower(),
                    )
                )
                code = medsl_state._district(state, re.sub(r"\.0$", "", district or ""))[0]
                tried = [f"{body}:{code}:g", f"{body}:{code}:gs"]
            key = next((keys[(state, year, rest)] for rest in tried if (state, year, rest) in keys), None)
            if key is not None and key not in filled:
                seats[(state, year, office, district)] = key
        print(f"{len(seats)} contests on file without a precinct set")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://doi.org/10.7910/DVN/YN4TLR",
            license="CC BY 3.0",
        )
        cited: set[str] = set()
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county'"
            ).fetchall()
        }
        minted: dict[str, tuple[str, str, date]] = {}
        skipped: dict[str, list[str]] = {}
        missed: list[tuple[float, str, int, int]] = []
        values = []
        for (state, year, office, district, file), rows in lines.group_by(
            "state", "year", "office", "district", "file", maintain_order=True
        ):
            key = seats.get((state, year, office, district))
            if key is None:
                continue
            day = date.fromisoformat(key[:10])
            contest, election, first, official = conn.execute(
                """select c.id, c.election_id, min(v.count_round), sum(v.value) filter (where v.measure = 1)::bigint
                   from external_id x join contest c on c.id = x.entity_id
                   join result_set rs on rs.contest_id = c.id and rs.is_default
                   join result_value v on v.result_set_k = rs.k and v.measure = 1
                   where x.scheme = 'us.contest' and x.value = %s group by 1, 2""",
                [key],
            ).fetchone() or (None, None, None, None)
            if contest is None or not official or first not in (0, 1):
                skipped.setdefault("no official total", []).append(state)
                continue
            options: dict[str, list[tuple[UUID, int]]] = {"d": [], "r": [], "o": []}
            labels: dict[UUID, str] = {}
            for option, label, parties, votes in conn.execute(
                """select o.id, o.ballot_label,
                          array(select k.value from external_id k where k.entity_id = o.party_id and k.scheme = 'us.party'),
                          coalesce((select sum(v.value) from result_value v
                                    where v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1),
                                   (select sum(v.value) from result_value v join contest_option p on p.k = v.option_k
                                    where p.parent_option_id = o.id and v.result_set_k = rs.k and v.measure = 1), 0)::bigint
                   from contest_option o join result_set rs on rs.contest_id = o.contest_id and rs.is_default
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall():
                democratic = any(
                    party == "democratic-party" or re.search(r"dem\w*-?farm|democratic-?nonpartisan", party)
                    for party in parties
                )
                republican = "republican-party" in parties
                side = "d" if democratic and not republican else "r" if republican and not democratic else "o"
                if votes:
                    options[side].append((option, votes))
                    labels[option] = label
            if len(options["d"]) > 1 or len(options["r"]) > 1 or not (options["d"] or options["r"]):
                skipped.setdefault("several or no major-party candidates", []).append(state)
                continue
            sums = rows.group_by("party").agg(pl.col("votes").sum())
            totals = {party: int(sums.filter(pl.col("party") == party)["votes"].sum()) for party in "dr"}
            ranked = sorted(
                [(votes, option) for side in "dro" for option, votes in options[side]], key=lambda pair: -pair[0]
            )
            counted = sorted(
                [
                    (totals[side] if side in "dr" else votes, option)
                    for side in "dro"
                    for option, votes in options[side]
                ],
                key=lambda pair: -pair[0],
            )
            off = [
                (totals[side] / votes, key, totals[side], votes)
                for side in "dr"
                for _, votes in options[side]
                if abs(totals[side] - votes) > max(10, votes * TOLERANCE)
            ]
            if off:
                missed.extend(off)
                skipped.setdefault("a precinct sum misses the official total", []).append(state)
                continue
            if ranked[0][1] != counted[0][1]:
                skipped.setdefault("another option comes first", []).append(state)
                continue
            result_set = cs.key(f"{SOURCE}.result_set", f"{key}:precinct")
            if result_set not in cs.minted:
                continue
            covered = sum(totals[side] for side in "dr" if options[side])
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
                completeness=round(covered / official, 4),
            )
            if options["o"]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=PARTIAL.format(others=", ".join(labels[option] for option, _ in options["o"])),
                )
            if file not in cited:
                cited.add(file)
                cs.artifact(raw.cached(SOURCE, FILE.format(id=files()[file])), source)
            picked = rows.filter(
                pl.col("party").is_in([side for side in "dr" if options[side]]), pl.col("votes") > 0
            ).with_columns(
                code=pl.concat_str(pl.lit(str(year)), "state", "fips", pl.lit(""), "precinct", separator=":")
            )
            for code, fips in picked.select("code", "fips").unique().iter_rows():
                minted.setdefault(code, (state, fips, day))
            values.append(
                picked.select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("code").map_elements(
                        lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String
                    ),
                    option_id=pl.col("party").replace_strict(
                        {side: str(options[side][0][0]) for side in "dr" if options[side]}
                    ),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.lit(1, pl.Int16),
                    count_round=pl.lit(first, pl.Int16),
                    flags=pl.lit(0, pl.Int16),
                    value="votes",
                )
            )
        for reason, states in skipped.items():
            print(f"{len(states)} contests skipped: {reason}", pl.Series(states).value_counts(sort=True).rows()[:12])
        for ratio, key, counted, official in sorted(missed)[:: max(1, len(missed) // 30)]:
            print(f"{key}: {counted} by precinct against {official} ({ratio:.3f})")
        existing = {
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code = any(%s)", [list(minted)]
            ).fetchall()
        }
        for code, (state, fips, day) in minted.items():
            if code in existing:
                continue
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", code),
                polity_id=cs.polity(f"US-{state}"),
                scheme_code="us.precinct",
                code=code,
                parent_id=counties[fips][0],
                area_relation="within",
                area_id=counties[fips][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        print(f"{len(values)} precinct sets, {len(minted) - len(existing)} new precincts, {len(existing)} reused")
        if values:
            cs.frame("result_value", pl.concat(values))
        return cs.write()
