import re
from collections import defaultdict
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us import county_measures, medsl, medsl_state
from ea.sources.us.klarner import LOWER
from ea.sources.us.medsl_precincts import METHOD
from ea.store import DATA, connect

SOURCE = "us.medsl_state_precincts"
VERSION = "4"
PARSED = DATA / "parsed" / SOURCE
STATEWIDE = DATA / "parsed" / "us.medsl_statewide"
STATES_2022 = {
    "AK": "10855159",
    "AL": "10855153",
    "AZ": "14249794",
    "CA": "10855141",
    "CO": "10855131",
    "CT": "10855136",
    "DE": "10855156",
    "FL": "10855171",
    "GA": "10855164",
    "HI": "10855177",
    "IA": "10855151",
    "ID": "13996909",
    "IL": "10855148",
    "IN": "10855180",
    "KS": "10855161",
    "KY": "10855135",
    "LA": "10855157",
    "MA": "10855170",
    "MD": "14249804",
    "ME": "10855138",
    "MN": "10855150",
    "MO": "10855176",
    "MT": "10855149",
    "NC": "10855142",
    "ND": "13996910",
    "NE": "10855155",
    "NH": "10855179",
    "NM": "10855137",
    "NV": "10855182",
    "NY": "10855145",
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
    "VT": "10855166",
    "WA": "10855160",
    "WI": "10855181",
    "WV": "10855172",
    "WY": "10855165",
}
FILES = {
    2016: [("us.medsl_measures", f"{medsl.DATAVERSE}3345337?format=original")],
    2018: [(SOURCE, f"{medsl.DATAVERSE}6692663")],
    2020: [(SOURCE, f"{medsl.DATAVERSE}13582896")],
    2022: [
        (
            "us.medsl_measures" if state in county_measures.MEDSL_2022 else SOURCE,
            f"{medsl.DATAVERSE}{file}?format=original",
        )
        for state, file in STATES_2022.items()
    ],
    2024: [(medsl_state.SOURCE, medsl_state.RETURNS)],
}
TITLES = {
    2016: ("State Precinct-Level Returns 2016", "GSZG1O"),
    2018: ("State Precinct-Level Returns 2018", "ZFXEJU"),
    2020: ("State Precinct-Level Returns 2020", "OKL2K1"),
    2022: ("Precinct-Level Returns 2022 by Individual State", "UYQIEP"),
    2024: (medsl_state.TITLE, "DODOBJ"),
}
GUESTBOOK = {
    "name": "",
    "email": "",
    "institution": "",
    "position": "",
}
SWAPPED = {
    ("04025", "JAY MARCIA"): "MARCIA SMITH",
    ("04025", "RUBY SMITH"): "JAY RUBY",
    ("36031", "BILLY D JONES"): "D BILLY JONES",
}
VERMONT = {
    "ADD": "ADDISON",
    "BEN": "BENNINGTON",
    "CAL": "CALEDONIA",
    "CHI": "CHITTENDEN",
    "ESX": "ESSEX",
    "FRA": "FRANKLIN",
    "GI": "GRANDISLE",
    "LAM": "LAMOILLE",
    "ORA": "ORANGE",
    "ORL": "ORLEANS",
    "RUT": "RUTLAND",
    "WAS": "WASHINGTON",
    "WDH": "WINDHAM",
    "WDR": "WINDSOR",
}
HAMPSHIRE = {
    "33001": "BELKNAP",
    "33003": "CARROLL",
    "33005": "CHESHIRE",
    "33007": "COOS",
    "33009": "GRAFTON",
    "33011": "HILLSBOROUGH",
    "33013": "MERRIMACK",
    "33015": "ROCKINGHAM",
    "33017": "STRAFFORD",
    "33019": "SULLIVAN",
}
ALIASES = {
    "womensequality": ("wep",),
    "workingfamilies": ("wor",),
    "conservative": ("constitution",),
    "constitution": ("conservative",),
}
ORDINALS = {
    word: number
    for number, word in enumerate(
        [
            "FIRST",
            "SECOND",
            "THIRD",
            "FOURTH",
            "FIFTH",
            "SIXTH",
            "SEVENTH",
            "EIGHTH",
            "NINTH",
            "TENTH",
            "ELEVENTH",
            "TWELFTH",
            "THIRTEENTH",
            "FOURTEENTH",
            "FIFTEENTH",
            "SIXTEENTH",
            "SEVENTEENTH",
            "EIGHTEENTH",
            "NINETEENTH",
            "TWENTIETH",
        ],
        1,
    )
} | {"THIRTIETH": 30}
ORDINALS |= {
    f"{tens}{separator}{word}": base + number
    for tens, base in (("TWENTY", 20), ("THIRTY", 30))
    for separator in ("-", " ")
    for word, number in list(ORDINALS.items())[:9]
}
POOLS = {"write-ins", "all-others", "scattering", "scatter", "others", "misc", "write-in", "other"}
BODIES = r"(house|senate|assembly|house-of-delegates|general-assembly|legislature)"


def fetch() -> None:
    medsl_state.fetch()
    for year, (source, url) in ((year, file) for year, files in FILES.items() for file in files):
        try:
            raw.cached(source, url)
        except LookupError:
            if source != SOURCE:
                raw.fetch(source, url, suffix=".csv")
                continue
            signed = httpx.post(url, json={"guestbookResponse": GUESTBOOK}, timeout=60).json()["data"]["signedUrl"]
            raw.fetch(SOURCE, signed, suffix=".csv")
            with raw.locked(SOURCE) as index:
                index[url] = index.pop(signed)
            print(f"{year}: {raw.cached(SOURCE, url).path}")


def parse() -> None:
    frame = pl.concat(
        [
            medsl.precincts({("state", year): raw.cached(source, url).path})
            for year, files in FILES.items()
            for source, url in files
        ],
        how="diagonal_relaxed",
    ).with_columns(
        pl.col("precinct", "jurisdiction").str.strip_chars(),
        seat=pl.col("seat")
        .str.strip_chars()
        .str.to_uppercase()
        .str.replace(r"\.0$", "")
        .str.replace(r"^(?:DISTRICT )?0*(\d+),? POSITION (\d)$", "DISTRICT $1 POSITION $2")
        .str.replace(r"^DISTRICT 0*(\d+)$", "$1")
        .str.replace(r" DISTRICT$", "")
        .map_elements(
            lambda seat: next(
                (
                    f"{number}TH {seat.removeprefix(word).strip()}"
                    for word, number in sorted(ORDINALS.items(), key=lambda item: -len(item[0]))
                    if seat.startswith(f"{word} ")
                ),
                seat,
            ),
            return_dtype=pl.String,
        ),
        candidate=pl.when(pl.col("year") == 2024)
        .then(
            pl.struct("code", "candidate").map_elements(
                lambda row: SWAPPED.get((row["code"], row["candidate"]), row["candidate"]), return_dtype=pl.String
            )
        )
        .otherwise("candidate"),
    )
    race = ["year", "state", "office", "seat"]
    recounted = frame.filter(pl.col("stage") == "GEN RECOUNT").select(*race).unique()
    frame = pl.concat(
        [
            frame.filter(pl.col("stage") == "GEN").join(recounted, on=race, how="anti"),
            frame.filter(pl.col("stage") == "GEN RECOUNT"),
        ]
    )
    homes = (
        frame.filter(pl.col("seat").is_not_null(), ~pl.col("writein"), pl.col("measure") == 1)
        .select("year", "state", "office", "candidate", "seat", "special")
        .unique()
        .filter(pl.len().over("year", "state", "office", "candidate") == 1)
        .rename({"seat": "home", "special": "home_special"})
    )
    frame = (
        frame.join(homes, on=["year", "state", "office", "candidate"], how="left")
        .with_columns(
            seat=pl.coalesce("seat", pl.when(~pl.col("writein")).then("home")),
            special=pl.when(pl.col("seat").is_null() & ~pl.col("writein"))
            .then(pl.coalesce("home_special", "special"))
            .otherwise("special"),
        )
        .filter(pl.col("seat").is_not_null(), ~pl.col("seat").is_in(["STATEWIDE", ""]))
        .with_columns(
            kind=pl.when(pl.col("kind").is_in(["county", "place"]) & (pl.col("precinct") != ""))
            .then(pl.lit("precinct"))
            .otherwise("kind"),
        )
        .with_columns(
            county=pl.when(pl.col("kind") == "precinct").then("code"),
            code=pl.when(pl.col("kind") == "precinct")
            .then(pl.concat_str("year", "state", "code", "jurisdiction", "precinct", separator=":"))
            .otherwise("code"),
        )
        .with_columns(
            seat=pl.when(
                (pl.col("state") == "NH") & (pl.col("office") == "STATE HOUSE") & pl.col("seat").str.contains(r"^\d+$")
            )
            .then(
                pl.coalesce("county", "code").str.slice(0, 5).replace_strict(HAMPSHIRE, default=None)
                + " "
                + pl.col("seat")
            )
            .otherwise("seat"),
        )
        .filter(pl.col("seat").is_not_null())
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.filter(pl.col("kind") == "precinct").group_by("code").agg(
        pl.col("state").first(), pl.col("county").first(), pl.col("day").min()
    ).sort("code").write_parquet(PARSED / "units.parquet")
    frame.group_by(
        "year",
        "day",
        "state",
        "office",
        "seat",
        "special",
        "kind",
        "code",
        "candidate",
        "party",
        "writein",
        "measure",
        maintain_order=True,
    ).agg(pl.col("votes").sum()).write_parquet(PARSED / "returns.parquet")
    print(frame.filter(pl.col("kind") == "precinct").group_by("year").agg(pl.col("code").n_unique()).sort("year"))


def emit() -> Path:
    mapped = pl.read_parquet(STATEWIDE / "contests.parquet")
    returns = pl.concat(
        [
            pl.read_parquet(PARSED / "returns.parquet").with_columns(mapped=pl.lit(None, pl.String)),
            pl.read_parquet(STATEWIDE / "returns.parquet")
            .join(mapped, on=["year", "state", "title", "post", "special"], nulls_equal=True)
            .select(
                "year",
                "day",
                "state",
                office="title",
                seat="post",
                special="special",
                kind="kind",
                code="code",
                candidate="candidate",
                party="party",
                writein="writein",
                measure="measure",
                votes="votes",
                mapped="key",
            ),
        ],
        how="vertical_relaxed",
    )
    days = sorted({str(day) for day in returns["day"].unique()})
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="State legislative and statewide office results by precinct 2016–2024, from the MIT Election Data and "
            "Science Lab",
            polity="US",
        )
        codes: dict[str, set[str]] = defaultdict(set)
        for scheme, code in conn.execute(
            "select a.scheme_code, c.code from area a join area_code c on c.area_id = a.id"
            " where a.scheme_code in ('us.sldu', 'us.sldl')"
        ).fetchall():
            codes[scheme].add(code)
        contests: dict[str, tuple[UUID, UUID]] = {}
        options: dict[UUID, dict[UUID, tuple[str | None, str, UUID | None, list[str], int]]] = defaultdict(dict)
        for key, contest, election, option, option_key, kind, parent, label, person, votes in conn.execute(
            f"""select cx.value, c.id, c.election_id, o.id, x.value, o.kind, o.parent_option_id, o.ballot_label,
                       (select n.text from candidacy cy join name n on n.entity_id = cy.person_id and n.is_preferred
                        where cy.option_id = o.id and cy.role = 'head' limit 1),
                       coalesce(sum(v.value), 0)::bigint
                from external_id cx join contest c on c.id = cx.entity_id
                join contest_option o on o.contest_id = c.id
                left join external_id x on x.entity_id = o.id and x.scheme = 'us.option'
                join result_set rs on rs.contest_id = c.id and rs.is_default
                left join result_value v on v.result_set_k = rs.k and v.option_k = o.k and v.measure = 1
                where cx.scheme = 'us.contest' and left(cx.value, 10) = any(%s)
                  and (cx.value ~ '^[0-9-]{{10}}:[A-Z]{{2}}:{BODIES}:' or cx.value = any(%s))
                group by 1, 2, 3, 4, 5, 6, 7, 8""",
            [days, mapped["key"].to_list()],
        ).fetchall():
            contests[key] = (contest, election)
            names = [slug(text) for text in (label, person) if text]
            options[contest][option] = (option_key, kind, parent, names, votes)
        counties = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code in ('us.county', 'us.place')"
            ).fetchall()
        }
        sources: dict[int, UUID] = {}
        for year, (title, doi) in TITLES.items():
            sources[year] = cs.source(medsl_state.PUBLISHER, title)
            if sources[year] in cs.minted:
                cs.add(
                    "source",
                    id=sources[year],
                    kind="academic",
                    publisher=medsl_state.PUBLISHER,
                    title=title,
                    url=f"https://doi.org/10.7910/DVN/{doi}",
                    license="CC0 1.0",
                    license_url="https://creativecommons.org/publicdomain/zero/1.0/",
                )
            for origin, url in FILES[year]:
                cs.artifact(raw.cached(origin, url), sources[year])
        own: dict[str, tuple[UUID, UUID | None, int, set[str]]] = {
            key: (result_set, supersedes, int(total), set(present))
            for key, result_set, supersedes, total, present in conn.execute(
                """select regexp_replace(x.value, ':r[0-9]+$', ''), r.id, r.supersedes_id,
                          (select coalesce(sum(v.value), 0) from result_value v
                           where v.result_set_k = r.k and v.measure = 1 and v.count_round = 0),
                          (select coalesce(array_agg(distinct case u.scheme_code when 'us.precinct'
                                  then split_part(u.code, ':', 3) else u.code end), '{}')
                           from result_value v join reporting_unit u on u.k = v.unit_k
                           where v.result_set_k = r.k and v.measure = 1 and v.value > 0)
                   from external_id x join result_set r on r.id = x.entity_id and r.status = 'active'
                   where x.scheme = %s""",
                [f"{SOURCE}.result_set"],
            ).fetchall()
        }
        values: list[pl.DataFrame] = []
        result_sets: list[UUID] = []
        taken: set[str] = set()
        for (year, day, state, office, seat, special, statewide), lines in returns.group_by(
            "year", "day", "state", "office", "seat", "special", "mapped", maintain_order=True
        ):
            total = int(lines.filter(pl.col("measure") == 1)["votes"].sum())
            key = statewide
            if statewide is None:
                if state == "NE":
                    body_key, scheme = "NE:legislature", "us.sldu"
                elif office == "STATE SENATE":
                    body_key, scheme = f"{state}:senate", "us.sldu"
                else:
                    chamber = LOWER.get(state, "House of Representatives")
                    body_key = f"{state}:{slug(chamber.removeprefix('State ').removesuffix(' of Representatives'))}"
                    scheme = "us.sldl"
                code, post = medsl_state._district(state, str(seat))
                if state == "VT" and re.fullmatch(r"[A-Z]+([- ][A-Z]+)*([- ]\d+){0,2}", str(seat)):
                    parts = re.split(r"[- ]", str(seat))
                    numbers = [part for part in parts if part.isdigit()]
                    code = "".join(VERMONT.get(part, part) for part in parts if not part.isdigit()).lower() + "".join(
                        f"-{number}" if index == 0 else f"-{chr(96 + int(number))}"
                        for index, number in enumerate(numbers)
                    )
                    post = None
                if f"{state}:{code}" not in codes[scheme]:
                    letters = sorted(code.split("-")[0])
                    number = code.split("-")[1] if "-" in code else None
                    near = [
                        known
                        for known in codes[scheme]
                        if known.startswith(f"{state}:")
                        and sorted(known.split(":")[1].split("-")[0]) == letters
                        and (known.split("-")[1] if "-" in known else None) == number
                    ]
                    if len(near) == 1:
                        code = near[0].split(":")[1]
                key = (
                    f"{day}:{state}:{body_key.split(':')[1]}:{code}"
                    + (f":{post}" if post else "")
                    + (":gs" if special else ":g")
                )
                base = key.rsplit(":", 1)[0]
                posted = [f"{base}:{number}:g" for number in range(1, 5) if f"{base}:{number}:g" in contests]
                key = next(
                    (
                        option
                        for option in (key, f"{base}:{'g' if special else 'gs'}", *(posted if len(posted) == 1 else []))
                        if option in contests and option not in taken
                    ),
                    key,
                )
            if key not in contests or key in taken:
                print(f"{key}: no contest ({office} {seat}), {total} skipped")
                continue
            taken.add(key)
            contest, election = contests[key]
            known = options[contest]
            children = {parent for _, _, parent, _, _ in known.values() if parent is not None}
            roots = {
                option: names
                for option, (_, kind, parent, names, _) in known.items()
                if parent is None and kind != "write_in"
            }
            pools = [
                option
                for option, (_, kind, parent, names, _) in known.items()
                if parent is None and (kind == "write_in" or set(names) & POOLS)
            ]
            others = [
                option for option, (_, kind, parent, _, _) in known.items() if parent is None and kind == "others"
            ]
            picked: dict[tuple[str, str | None], UUID | None] = {}
            unmatched = 0
            leading = {
                candidate: party
                for candidate, party in lines.filter(pl.col("measure") == 1)
                .group_by("candidate", "party")
                .agg(pl.col("votes").sum())
                .sort("votes")
                .select("candidate", "party")
                .iter_rows()
            }
            for candidate, party, writein, votes in (
                lines.filter(pl.col("measure") == 1)
                .group_by("candidate", "party", "writein", maintain_order=True)
                .agg(pl.col("votes").sum())
                .iter_rows()
            ):
                root = None
                if candidate in medsl_state.DROPPED and not writein:
                    continue
                if writein:
                    if len(pools) > 1:
                        unmatched += votes
                        continue
                    picked[(candidate, party)] = pools[0] if pools else others[0] if len(others) == 1 else None
                    continue
                labeled = [option for option, names in roots.items() if names[:1] == [slug(candidate)]]
                exact = [option for option, names in roots.items() if slug(candidate) in names]
                same = next((group for group in (labeled, exact) if group), [])
                if not same:
                    words = [
                        word
                        for word in re.sub(r'"[^"]*"', " ", re.split(r" AND | & |/", candidate)[0]).split()
                        if word not in {"JR", "SR", "II", "III", "IV"}
                    ]
                    if len(words) > 1 and len(words[-1]) == 1:
                        words = [words[-1], *words[:-1]]
                    surname = re.sub(r"[^a-z]", "", slug(words[-1])) if words else ""
                    if surname:
                        initial = slug(words[0])[:1]
                        same = [
                            option
                            for option, names in roots.items()
                            if any(
                                re.sub(r"[^a-z]", "", re.sub(r"-(jr|sr|ii|iii|iv)$", "", name)).endswith(surname)
                                or f"-{surname}-" in f"-{name}-"
                                for name in names
                            )
                        ]
                        first = [option for option in same if any(name[:1] == initial for name in roots[option])]
                        same = first if len(first) == 1 else same
                minor = [option for option in same if any(name.endswith("-nm") for name in roots[option])]
                if minor and len(same) - len(minor) == 1:
                    same = (
                        minor if party != leading.get(candidate) else [option for option in same if option not in minor]
                    )
                    same = same if len(same) == 1 else []
                if len(same) > 1 and party:
                    word = slug(party)[:5]
                    tagged = [
                        option
                        for option in same
                        if any(token.startswith(word) for name in roots[option] for token in name.split("-")[2:])
                    ]
                    same = (
                        tagged if tagged else [min(same, key=lambda option: min(len(name) for name in roots[option]))]
                    )
                root = same[0] if len(same) == 1 else None
                if root is not None and root in children:
                    wanted = re.sub(r"[^a-z]", "", slug(party or "no party"))

                    stem = roots[root][0] if roots[root] else ""
                    tags = {
                        option: re.sub(r"[^a-z]", "", names[0].removeprefix(stem)) if names else ""
                        for option, (_, _, parent, names, _) in known.items()
                        if parent == root
                    }
                    variants = {wanted, *ALIASES.get(wanted, ())}
                    lines_of = (
                        [option for option, tag in tags.items() if tag == wanted]
                        or [option for option, tag in tags.items() if tag and wanted[:5] in tag]
                        or [option for option, tag in tags.items() if tag in variants]
                        or [option for option, tag in tags.items() if tag in ("nm", "unspecifiednonmaj", "other")]
                    )
                    root = lines_of[0] if len(lines_of) == 1 else None
                if root is None and len(others) == 1:
                    root = others[0]
                if root is None:
                    unmatched += votes
                    continue
                picked[(candidate, party)] = root
            tallies: dict[UUID, int] = defaultdict(int)
            for candidate, party, votes in (
                lines.filter(pl.col("measure") == 1).select("candidate", "party", "votes").iter_rows()
            ):
                if (option := picked.get((candidate, party))) is not None:
                    tallies[option] += votes
            official = sum(votes for _, _, _, _, votes in known.values())
            drifted = [
                (sorted(names)[:1], tallies.get(option, 0), votes)
                for option, (_, _, _, names, votes) in known.items()
                if official
                and option not in children
                and not (known[option][1] == "write_in" and votes == 0)
                and tallies.get(option, 0) - votes > max(10, votes * 0.01)
            ]
            if drifted:
                print(f"{key}: precinct sums exceed the district totals {drifted[:3]}, skipped")
                continue
            if unmatched > max(10, (official or total) * 0.01):
                print(f"{key}: {unmatched} votes match no option, skipped")
                continue
            members: dict[str, str] = {}
            for unit_code, kind in lines.select("code", "kind").unique().iter_rows():
                if kind in ("county", "place"):
                    if unit_code not in counties:
                        raise ValueError(f"{key}: no county {unit_code}")
                    members[unit_code] = str(counties[unit_code][0])
                elif kind == "precinct":
                    members[unit_code] = str(cs.unit("us.precinct", unit_code))
                else:
                    unit = cs.unit("us.ak_house_district" if kind == "district" else "us.aggregate", unit_code)
                    if unit not in cs.minted:
                        members[unit_code] = str(unit)
            if set(lines["code"]) - set(members):
                print(f"{key}: no unit for {sorted(set(lines['code']) - set(members))}, skipped")
                continue
            counted = sum(
                votes
                for candidate, party, votes in lines.filter(pl.col("measure") == 1)
                .select("candidate", "party", "votes")
                .iter_rows()
                if (candidate, party) in picked
            )
            published = own.get(key)
            if published is not None and (
                published[2] == counted
                or not {
                    code.split(":")[2] if kind == "precinct" else code
                    for code, kind, votes in lines.filter(pl.col("measure") == 1)
                    .select("code", "kind", "votes")
                    .iter_rows()
                    if votes > 0
                }
                - published[3]
            ):
                continue
            result_set = cs.key(f"{SOURCE}.result_set", key if published is None else f"{key}:r{counted}")
            if result_set not in cs.minted:
                continue
            result_sets.append(result_set)
            if None in picked.values():
                pool = cs.key("us.option", f"{key}:write-ins")
                cs.add("contest_option", id=pool, contest_id=contest, kind="write_in", ballot_label="Write-ins")
                picked = {line: pool if option is None else option for line, option in picked.items()}
            if published is not None:
                print(f"{key}: {published[2]} votes on file, {counted} parsed")
                cs.add(
                    "result_set",
                    id=published[0],
                    election_id=election,
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="superseded",
                    source_id=sources[year],
                    is_default=False,
                    supersedes_id=published[1],
                )
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="active",
                source_id=sources[year],
                is_default=False,
                supersedes_id=None if published is None else published[0],
                completeness=round(sum(tallies.values()) / official, 4) if official else None,
            )
            values.append(
                lines.join(
                    pl.DataFrame(
                        [(candidate, party, str(option)) for (candidate, party), option in picked.items()],
                        schema={"candidate": pl.String, "party": pl.String, "option": pl.String},
                        orient="row",
                    ),
                    on=["candidate", "party"],
                    how="left",
                    nulls_equal=True,
                )
                .filter((pl.col("measure") != 1) | pl.col("option").is_not_null())
                .select(
                    result_set_id=pl.lit(str(result_set)),
                    unit_id=pl.col("code").replace_strict(members),
                    option_id=pl.when(pl.col("measure") == 1).then("option"),
                    channel=pl.lit(0, pl.Int16),
                    measure=pl.col("measure"),
                    count_round=pl.lit(0, pl.Int16),
                    flags=pl.when(pl.col("kind") == "aggregate")
                    .then(pl.lit(32, pl.Int16))
                    .otherwise(pl.lit(0, pl.Int16)),
                    value="votes",
                )
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(pl.col("value").sum())
            )
        frame = pl.concat(values)
        shaped: dict[str, str] = {
            code: str(area)
            for code, area in conn.execute(
                """select c.code, c.area_id from area_code c join area a on a.id = c.area_id
                   where a.scheme_code = 'us.precinct' and c.code ~ '^20(16|18|20|22|24):'"""
            ).fetchall()
        }
        existing = {
            code
            for (code,) in conn.execute(
                "select code from reporting_unit where scheme_code = 'us.precinct' and code ~ '^20(16|18|20|22|24):'"
            ).fetchall()
        }
        used = set(frame["unit_id"].unique())
        units = (
            pl.concat(
                [pl.read_parquet(PARSED / "units.parquet"), pl.read_parquet(STATEWIDE / "units.parquet")],
                how="vertical_relaxed",
            )
            .group_by("code")
            .agg(pl.col("state").first(), pl.col("county").first(), pl.col("day").min())
            .filter(~pl.col("code").is_in(list(existing)))
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)))
        )
        cs.frame(
            "reporting_unit",
            units.select(
                "id",
                polity_id=pl.col("state").map_elements(
                    lambda state: str(cs.polity(f"US-{state}")), return_dtype=pl.String
                ),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("county").replace_strict({code: str(unit) for code, (unit, _) in counties.items()}),
                area_relation=pl.when(pl.col("code").is_in(list(shaped)))
                .then(pl.lit("equals"))
                .otherwise(pl.lit("within")),
                area_id=pl.col("code").replace_strict(
                    shaped,
                    default=pl.col("county").replace_strict({code: str(area) for code, (_, area) in counties.items()}),
                ),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        cs.frame("result_value", frame)
        for result_set in result_sets:
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD,
                valid_from_precision="day",
                valid_to_precision="day",
            )
        print(f"{len(result_sets)} result sets, {frame.height} values, {units.height} new precincts")
        return cs.write()
