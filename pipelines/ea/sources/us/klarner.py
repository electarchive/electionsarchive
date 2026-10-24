import os
import re
import subprocess
import sys
from collections import Counter
from datetime import date
from pathlib import Path
from uuid import UUID

import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.names import initials
from ea.sources.us.ak_dor import CONTESTS as RANKED_ROUNDS
from ea.store import DATA, connect

SOURCE = "us.klarner"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
ACCESS = "https://dataverse.harvard.edu/api/access/datafile"
RETURNS = f"{ACCESS}/10273085"
CODEBOOK = f"{ACCESS}/10273083"
PUBLISHER = "Carl Klarner"
TITLE = "State Legislative Election Returns, 1967-2022"
COLUMNS = {
    "caseid": pl.Int64,
    "year": pl.Int32,
    "month": pl.Int32,
    "day": pl.Int32,
    "sab": pl.String,
    "sfips": pl.Int32,
    "sen": pl.Int32,
    "ddez": pl.String,
    "dname": pl.String,
    "dno": pl.String,
    "geopost": pl.Int32,
    "mmdpost": pl.String,
    "dtype": pl.Int32,
    "etype": pl.String,
    "eseats": pl.Int32,
    "cand": pl.String,
    "candid": pl.String,
    "party": pl.String,
    "partyz": pl.String,
    "vote": pl.Int64,
    "outcome": pl.String,
    "last": pl.String,
    "first": pl.String,
    "middle": pl.String,
    "suffix": pl.String,
}
CONTEST = ["year", "month", "day", "etype", "sab", "sen", "dname", "dno", "geopost", "mmdpost"]
LOWER = {
    "CA": "State Assembly",
    "NV": "Assembly",
    "NY": "State Assembly",
    "WI": "State Assembly",
    "NJ": "General Assembly",
    "MD": "House of Delegates",
    "VA": "House of Delegates",
    "WV": "House of Delegates",
}
UPPER = {"CA": "State Senate", "NY": "State Senate"}
NATIONAL = {
    "libertarian": "Libertarian Party",
    "green": "Green Party",
    "reform": "Reform Party",
    "constitution": "Constitution Party",
    "naturallaw": "Natural Law Party",
    "socialistworkers": "Socialist Workers Party",
    "prohibition": "Prohibition Party",
    "americanindependent": "American Independent Party",
}
STATE_PARTIES = {
    "conservative": "Conservative Party",
    "workingfamilies": "Working Families Party",
    "working": "Working Families Party",
    "wor": "Working Families Party",
    "liberal": "Liberal Party",
    "righttolife": "Right to Life Party",
    "independence": "Independence Party",
    "independentparty": "Independent Party",
    "american": "American Party",
    "constitutional": "Constitutional Party",
    "peaceandfreedom": "Peace and Freedom Party",
    "independentamerican": "Independent American Party",
    "wep": "Women's Equality Party",
    "progressive": "Progressive Party",
    "libertyunion": "Liberty Union Party",
    "mountain": "Mountain Party",
    "ustaxpayers": "U.S. Taxpayers Party",
    "ust": "U.S. Taxpayers Party",
    "taxpayers": "Taxpayers Party",
    "unitedstateslabor": "U.S. Labor Party",
    "alaskanindependence": "Alaskan Independence Party",
    "socialist": "Socialist Party",
    "socialistlabor": "Socialist Labor Party",
    "razaunida": "Raza Unida Party",
    "larazaunida": "Raza Unida Party",
    "newalliance": "New Alliance Party",
    "aconnecticut": "A Connecticut Party",
    "connecticutforlieberman": "Connecticut for Lieberman",
    "unitedutah": "United Utah Party",
    "communist": "Communist Party",
    "populist": "Populist Party",
    "citizens": "Citizens Party",
    "concernedcitizens": "Concerned Citizens Party",
    "unitedcitizens": "United Citizens Party",
    "nationaldemocraticpartyofalabama": "National Democratic Party of Alabama",
    "freelibertarian": "Free Libertarian Party",
    "labor": "Labor Party",
}
INDEPENDENT = {
    "independent",
    "unenrolled",
    "nominatedbypetition",
    "nominationbypetitionnumber2",
    "petitioningcandidate",
    "petitioningcandidatect",
    "noaffiliation",
    "nopartyaffiliation",
    "unaffiliated",
    "npaflorida",
    "independentnonpartisan",
    "secondindependent",
    "thirdindependent",
    "4007",
}
PRIMARIES = {
    "indep": ("Independence", "independence"),
    "conservative": ("Conservative", "conservative"),
    "liber": ("Libertarian", "libertarian"),
    "green": ("Green", "green"),
    "thegreen": ("Green", "green"),
    "work": ("Working Families", "workingfamilies"),
    "const": ("Constitution", "constitution"),
    "amer": ("American", "american"),
    "indepamer": ("Independent American", "independentamerican"),
    "amerindep": ("American Independent", "americanindependent"),
    "prog": ("Progressive", "progressive"),
    "libertyunion": ("Liberty Union", "libertyunion"),
    "mountain": ("Mountain", "mountain"),
    "7thindep": ("Independence", "independence"),
    "constamerindep": ("Constitution and American Independent", None),
    "indeppartyofdel": ("Independent Party of Delaware", None),
    "petitioningcand": ("petitioning candidates'", None),
    "una": ("unaffiliated", None),
}
MISDATED = {"1974-11-04": "1974-11-05"}
UNDATED = {("ID", 2016, 11): (5, 17)}
CORRECTED = {
    398272: (2393, "l"),
    398273: (3586, "w"),
    398299: (4288, "w"),
    398409: (3256, "w"),
    398410: (2819, "l"),
    321600: (2086, "withdrew"),
}
RELOCATED = {caseid: ("29", "2838", "harri anne") for caseid in (335434, 335435, 335436)}
OUTCOMES = {335434: "w", 335435: "w", 335436: "w", 335475: "l", 335476: "l", 335477: "l"}
RESTAGED = {"2014-11-04:AL:senate:24:g"}
RETIRED = {
    (
        "us.option",
        "2014-11-04:AL:senate:24:g:335434",
    ): "Harri Anne Smith ran in Senate District 29, not 24 (Alabama SOS "
    "certified 2014 general results); the Klarner line filed under District 24 was moved to District 29.",
    ("us.candidacy", "2014-11-04:AL:senate:24:g:335434:head"): "Harri Anne Smith ran in Senate District 29, not 24 "
    "(Alabama SOS certified 2014 general results); the Klarner line filed under District 24 was moved to District 29.",
}
WITHDREW = {
    321600: 'Withdrew from the July 15 runoff on June 12, 2014, leaving David Faulkner the nominee (al.com, "Steve '
    'French drops out of Alabama House District 46 race, will support David Faulkner," June 12, 2014).'
}
VA_CLEANUP = {*range(392452, 392456), *range(392585, 392589)}
SUFFIXES = {"jr": "Jr.", "sr": "Sr."}
RANK = {"w": 0, "t": 1, "r": 2, "withdrew": 3}
MONTHS = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def fetch() -> None:
    raw.fetch(SOURCE, RETURNS, suffix=".tab")
    raw.fetch(SOURCE, CODEBOOK, suffix=".docx")


def parse() -> None:
    frame = (
        pl.read_csv(raw.cached(SOURCE, RETURNS).path, separator="\t", infer_schema_length=0, quote_char=None)
        .select(list(COLUMNS))
        .with_columns(pl.all().str.strip_chars('"').str.replace_all(r'\\"', '"').replace("", None))
        .cast(pl.Schema(COLUMNS))
        .filter(~pl.col("etype").is_in(["appointed", "filer"]), pl.col("month").is_not_null())
        .with_columns(
            vote=pl.col("caseid").replace_strict(
                {caseid: votes for caseid, (votes, _) in CORRECTED.items()},
                default=pl.col("vote"),
                return_dtype=pl.Int64,
            ),
            outcome=pl.col("caseid").replace_strict(
                {caseid: outcome for caseid, (_, outcome) in CORRECTED.items()} | OUTCOMES,
                default=pl.col("outcome"),
                return_dtype=pl.String,
            ),
        )
        .with_columns(
            *(
                pl.col("caseid")
                .replace_strict(
                    {caseid: fields[index] for caseid, fields in RELOCATED.items()},
                    default=pl.col(column),
                    return_dtype=pl.String,
                )
                .alias(column)
                for index, column in enumerate(("dno", "candid", "first"))
            )
        )
    )
    lines = (
        frame.group_by(*CONTEST, "candid", "party", "partyz", maintain_order=True)
        .agg(
            pl.col("caseid").min(),
            pl.col("vote").sum().alias("votes"),
            pl.col("vote").is_null().any().alias("missing"),
            *(
                pl.col(column).first()
                for column in COLUMNS
                if column not in {*CONTEST, "candid", "party", "partyz", "caseid", "vote"}
            ),
        )
        .with_columns(votes=pl.when(pl.col("missing")).then(None).otherwise(pl.col("votes")))
        .drop("missing")
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    lines.write_parquet(PARSED / "lines.parquet")


def emit() -> Path | str:
    lines = pl.read_parquet(PARSED / "lines.parquet").filter(~pl.col("caseid").is_in(VA_CLEANUP)).sort("caseid")
    if (chunk := os.environ.get("KLARNER_STATES")) is None:
        chunks: list[list[str]] = [[]]
        size = 0
        for sab, count in lines.group_by("sab").len().sort("sab").iter_rows():
            if size + count > 60000 and chunks[-1]:
                chunks.append([])
                size = 0
            chunks[-1].append(sab)
            size += count
        return "\n".join(
            subprocess.run(
                [sys.argv[0], "emit", SOURCE],
                env=os.environ | {"KLARNER_STATES": ",".join(states)},
                check=True,
                stdout=subprocess.PIPE,
                text=True,
            ).stdout.strip()
            for states in chunks
        )
    lines = lines.filter(pl.col("sab").is_in(chunk.split(",")))
    for (sab, year, month), (fixed, day) in UNDATED.items():
        undated = (
            (pl.col("sab") == sab) & (pl.col("year") == year) & (pl.col("month") == month) & pl.col("day").is_null()
        )
        lines = lines.with_columns(
            month=pl.when(undated).then(fixed).otherwise("month"), day=pl.when(undated).then(day).otherwise("day")
        )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"State legislative general, special, and primary elections 1967–2022 by district ({chunk})",
            polity="US",
        )
        zeroed = cs.zeroed()

        staged_names: dict[UUID, str] = {}

        def name(entity: UUID, text: str) -> None:
            staged_names[entity] = text

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://doi.org/10.7910/DVN/FJOGJB",
            license="CC0 1.0",
        )
        for url in (RETURNS, CODEBOOK):
            cs.artifact(raw.cached(SOURCE, url), source)

        nation = cs.polity("US")
        states: dict[str, str] = dict(
            conn.execute(
                """select substr(p.iso_code, 4), n.text from polity p
                   join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
                   where p.iso_code like 'US-%'"""
            ).fetchall()
        )
        elections_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.election'").fetchall()
        }
        published: dict[UUID, UUID | None] = dict(
            conn.execute(
                """select o.id, o.party_id from contest_option o join changeset c on c.id = o.changeset_id
                   where c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        )
        owned = {
            entity
            for (entity,) in conn.execute(
                """select x.id from (select id, changeset_id from contest union all select id, changeset_id from candidacy) x
                   join changeset c on c.id = x.changeset_id where c.pipeline = %s""",
                [SOURCE],
            ).fetchall()
        }
        chained = {
            contest
            for (contest,) in conn.execute(
                """select c.previous_round_id from contest c join contest p on p.id = c.previous_round_id
                   where c.election_id <> p.election_id"""
            ).fetchall()
        }
        demoted: dict[UUID, str] = dict(
            conn.execute(
                """select r.id, r.status::text from result_set r join changeset c on c.id = r.changeset_id
                   join external_id e on e.entity_id = r.id and e.scheme = %s
                   where c.pipeline <> %s and not r.is_default""",
                [f"{SOURCE}.result_set", SOURCE],
            ).fetchall()
        )
        parties_known = {
            value for (value,) in conn.execute("select value from external_id where scheme = 'us.party'").fetchall()
        }

        for scheme, noun in (("us.sldu", "state senate district"), ("us.sldl", "state house district")):
            cs.add("scheme", code=scheme, polity_id=nation, kind="electoral_district", rank=2, noun=noun)

        bodies: dict[tuple[str, int], tuple[UUID, str, str]] = {}
        for sab, sen in lines.select("sab", "sen").unique().sort("sab", "sen").iter_rows():
            if sab == "NE":
                chamber, key, kind = "Legislature", "NE:legislature", "unicameral"
            elif sen == 1:
                chamber = UPPER.get(sab, "Senate")
                key, kind = f"{sab}:senate", "upper_house"
            else:
                chamber = LOWER.get(sab, "House of Representatives")
                key = f"{sab}:{slug(chamber.removeprefix('State ').removesuffix(' of Representatives'))}"
                kind = "lower_house"
            body = cs.key("us.body", key)
            cs.add("body", id=body, polity_id=cs.polity(f"US-{sab}"), kind=kind)
            name(body, f"{states[sab]} {chamber}")
            bodies[(sab, sen)] = (body, f"{states[sab]} {chamber}", key.split(":")[1])

        lines = lines.with_columns(
            code=pl.concat_str(
                pl.col("dname"),
                pl.col("dno"),
                pl.when(pl.col("geopost") <= 26)
                .then(pl.col("geopost").add(96).cast(pl.UInt8).map_elements(chr, return_dtype=pl.String))
                .otherwise(pl.col("geopost").cast(pl.String)),
                separator="-",
                ignore_nulls=True,
            )
        )
        districts: dict[tuple[str, int, str], tuple[UUID, UUID, str]] = {}
        for (sab, sen, code), group in lines.group_by("sab", "sen", "code"):
            labels = Counter(group.filter(pl.col("mmdpost").is_null())["ddez"].drop_nulls().to_list())
            first = group.row(0, named=True)
            built = " ".join(
                part
                for part in (
                    (first["dname"] or "").title() or None,
                    (first["dno"] or "")
                    + (chr(64 + first["geopost"]) if first["geopost"] and first["geopost"] <= 26 else ""),
                )
                if part
            )
            label = labels.most_common(1)[0][0] if labels else built
            scheme = "us.sldu" if sen == 1 else "us.sldl"
            full = f"{sab}:{code}"
            area = cs.area(scheme, full)
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=full, valid_from=None)
            cs.add("area_parent", area_id=area, parent_id=cs.area("us.state", f"{first['sfips']:02}"), valid_from=None)
            shown = f"District {label}" if label[:1].isdigit() else label or code
            name(area, f"{bodies[(sab, sen)][1]}, {shown}")
            unit = cs.unit(scheme, full)
            cs.add(
                "reporting_unit",
                id=unit,
                polity_id=cs.polity(f"US-{sab}"),
                scheme_code=scheme,
                code=full,
                area_relation="equals",
                area_id=area,
                kind="standard",
            )
            districts[(sab, sen, code)] = (area, unit, shown)

        def classify(etype: str) -> tuple[str, str, str | None, bool]:
            runoff = etype.endswith("runoff")
            base = re.sub(r"(fpartunsettled|funsettled|fsettled|doesnotcount|runoff|f|\d)$", "", etype)
            if base in ("g", "la"):
                kind, label, scope = "general", "election", None
            elif base in ("gs", "ssg", "s", "las"):
                kind, label, scope = "special", "special election", None
            elif base == "nps":
                kind, label, scope = "special", "nonpartisan special election", None
            elif base in ("dps", "dpsg", "sdp"):
                kind, label, scope = "special", "Democratic special primary", "d"
            elif base == "srp":
                kind, label, scope = "special", "Republican special primary", "r"
            elif base == "dp":
                kind, label, scope = "primary", "Democratic primary", "d"
            elif base == "rp":
                kind, label, scope = "primary", "Republican primary", "r"
            elif base == "up":
                kind, label, scope = "primary", "primary", None
            elif base == "ttp":
                kind, label, scope = "primary", "top-two primary", None
            elif base.endswith("p") and base[:-1] in PRIMARIES:
                text, scope = PRIMARIES[base[:-1]]
                kind, label = "primary", f"{text} primary"
            else:
                raise ValueError(f"unknown etype {etype}")
            if etype == "gs":
                kind = "general"
            if runoff:
                return "runoff", "runoff" if label == "election" else f"{label} runoff", scope, True
            return kind, label, scope, False

        principals = {cs.key("us.party", "democratic-party"), cs.key("us.party", "republican-party")}

        def party(sab: str, label: str | None, partyz: str) -> tuple[UUID | None, bool]:
            if partyz == "d" and label not in STATE_PARTIES:
                return cs.key("us.party", "democratic-party"), False
            if partyz == "r" and label not in STATE_PARTIES:
                return cs.key("us.party", "republican-party"), False
            if label is None or partyz in ("w", "b", "m", "np"):
                return None, label in INDEPENDENT
            if label in INDEPENDENT:
                return None, True
            text = NATIONAL.get(label) or STATE_PARTIES.get(label)
            if text is None:
                return None, False
            candidates = [f"{sab}:{slug(text)}", f"{sab}:{slug(text.removesuffix(' Party'))}"]
            if label in NATIONAL:
                candidates.append(slug(text))
            key = next((key for key in candidates if key in parties_known), candidates[-1])
            id = cs.key("us.party", key)
            if id in cs.minted and key not in parties_known:
                parties_known.add(key)
                cs.add(
                    "party",
                    id=id,
                    kind="party",
                    polity_id=nation if label in NATIONAL else cs.polity(f"US-{sab}"),
                )
                name(id, text)
            return id, False

        def title(text: str) -> str:
            return re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text.title())

        def person_name(row: dict[str, object]) -> str:
            first, middle, last, suffix = (row[column] for column in ("first", "middle", "last", "suffix"))
            if not isinstance(last, str):
                return initials(title(str(row["cand"])))
            if (
                isinstance(first, str)
                and isinstance(middle, str)
                and first.lower() == middle.lower()
                and len(first) > 1
            ):
                given = re.sub(r"\(.*?\)|\d+", " ", str(row["cand"]).split(",")[1]).split()
                first, middle = given[0], " ".join(given[1:]) or None
            parts = [
                title(first) if isinstance(first, str) else None,
                (title(middle) + ("." if len(middle) == 1 else "")) if isinstance(middle, str) else None,
                title(last),
            ]
            text = " ".join(part for part in parts if part)
            if isinstance(suffix, str):
                text += f", {SUFFIXES.get(suffix, suffix.upper())}"
            return initials(text)

        days: dict[tuple[str, int, int, int | None, str], tuple[str, date, str]] = {}
        dated = (
            lines.select("sab", "year", "month", "day", "etype")
            .unique()
            .with_columns(kind=pl.col("etype").map_elements(lambda etype: classify(etype)[0], return_dtype=pl.String))
        )
        on_day = {
            (sab, year, month, day, kind)
            for sab, year, month, day, kind in dated.select("sab", "year", "month", "day", "kind").iter_rows()
        }
        elections: dict[tuple[str, int, int, int | None, str], UUID] = {}
        slugs: dict[str, UUID | None] = dict(conn.execute("select path, entity_id from slug").fetchall())

        def unique(path: str, entity: UUID | None = None) -> str:
            if path in slugs and (entity is None or slugs[path] != entity):
                raise ValueError(f"slug {path} is taken")
            slugs[path] = entity
            return path

        cs.renamed.update({right: wrong for wrong, right in MISDATED.items()})
        cs.renamed.update(
            {
                f"{year}-{fixed:02}-{day:02}:{sab}:": f"{year}-{month:02}:{sab}:"
                for (sab, year, month), (fixed, day) in UNDATED.items()
            }
        )
        merged = {entity for entities in cs.merged.values() for entity in entities}
        for wrong, right in MISDATED.items():
            for value in sorted(value for value in elections_known if value.startswith(wrong)):
                duplicate = cs.key("us.election", value)
                if str(duplicate) not in merged and value.replace(wrong, right) in elections_known:
                    cs.merge(duplicate, cs.key("us.election", value.replace(wrong, right)))
        for sab, year, month, day, kind in sorted(on_day, key=lambda row: (row[0], row[1], row[2], row[3] or 0)):
            original = kind
            suffix = {"general": "", "primary": ":primary", "runoff": ":runoff", "special": ":special"}[kind]
            if kind == "special" and day is not None:
                scoped = all(
                    classify(etype)[2] is not None
                    for etype in dated.filter(
                        pl.col("sab") == sab,
                        pl.col("year") == year,
                        pl.col("month") == month,
                        pl.col("day").eq_missing(day),
                        pl.col("kind") == kind,
                    )["etype"]
                )
                for other, other_suffix in (
                    (("primary", ":primary"),) if scoped else (("general", ""), ("primary", ":primary"))
                ):
                    if (sab, year, month, day, other) in on_day or (
                        f"{year}-{month:02}-{day:02}:{sab}{other_suffix}" in elections_known
                    ):
                        kind, suffix = other, other_suffix
                        break
            precision = "day"
            if day is None:
                matches = sorted(
                    value
                    for value in elections_known
                    if value.startswith(f"{year}-{month:02}-")
                    and value.endswith(f":{sab}{suffix}")
                    and value.split(":")[0] not in MISDATED
                )
                if f"{year}-{month:02}:{sab}{suffix}" in elections_known:
                    datekey, precision = f"{year}-{month:02}", "month"
                elif len(matches) == 1:
                    datekey = matches[0].split(":")[0]
                else:
                    datekey, precision = f"{year}-{month:02}", "month"
            else:
                datekey = MISDATED.get(f"{year}-{month:02}-{day:02}", f"{year}-{month:02}-{day:02}")
            on = date.fromisoformat(datekey if precision == "day" else f"{datekey}-01")
            key = f"{datekey}:{sab}{suffix}"
            election = cs.key("us.election", key)
            if election in cs.minted and key not in elections_known:
                elections_known.add(key)
                parent = None
                if kind == "general" and month == 11 and year % 2 == 0:
                    parent = cs.key("us.election", datekey)
                    if parent in cs.minted and datekey not in elections_known:
                        elections_known.add(datekey)
                        cs.add(
                            "election",
                            id=parent,
                            polity_id=nation,
                            kind="general",
                            date_from=on,
                            date_to=on,
                            date_precision="day",
                            status="held",
                        )
                        name(parent, f"{year} United States general election")
                        cs.add("slug", path=unique(f"us/{datekey}-general"), entity_id=parent)
                cs.add(
                    "election",
                    id=election,
                    polity_id=cs.polity(f"US-{sab}"),
                    parent_id=parent,
                    kind=kind,
                    date_from=on,
                    date_to=on,
                    date_precision=precision,
                    status="held",
                )
                when = (
                    f"{MONTHS[month - 1]} {day}, {year}"
                    if kind in ("special", "runoff") and day is not None
                    else f"{MONTHS[month - 1]} {year}"
                    if precision == "month"
                    else str(year)
                )
                name(election, f"{when} {states[sab]} {kind} election")
                cs.add("slug", path=unique(f"us/{sab.lower()}/{datekey}-{kind}"), entity_id=election)
            for etype in dated.filter(
                pl.col("sab") == sab,
                pl.col("year") == year,
                pl.col("month") == month,
                pl.col("day").eq_missing(day),
                pl.col("kind") == original,
            )["etype"]:
                elections[(sab, year, month, day, etype)] = election
                days[(sab, year, month, day, etype)] = (datekey, on, kind)

        systems = {
            key: cs.key("us.electoral_system", key)
            for key in ("fptp", "top_two", "two_round", "two_round_40", "two_round_45", "block", "cumulative")
        }
        if cs.minted & (set(systems.values()) - {systems["cumulative"]}):
            raise ValueError("the fptp, top-two, two-round, and block systems must exist")
        if systems["cumulative"] in cs.minted:
            cs.add(
                "electoral_system",
                id=systems["cumulative"],
                polity_id=nation,
                rules={"formula": "cumulative"},
                rules_version=1,
            )
            name(systems["cumulative"], "Cumulative vote")
        persons: dict[str, UUID] = {}
        for candid, group in lines.filter(pl.col("cand") != "scattering").group_by("candid"):
            persons[str(candid[0])] = cs.key(f"{SOURCE}.person", str(candid[0]))
            cs.add("person", id=persons[str(candid[0])])
            name(persons[str(candid[0])], person_name(group.row(-1, named=True)))

        contests: dict[tuple[object, ...], tuple[UUID, date]] = {}
        values: list[tuple[str, str, str, int]] = []
        dating = ["sab", "year", "month", "day", "etype"]
        ordered = (
            lines.join(
                pl.DataFrame(
                    [(*key, value[1]) for key, value in days.items()],
                    schema={**{column: lines.schema[column] for column in dating}, "on": pl.Date},
                    orient="row",
                ),
                on=dating,
                how="left",
                nulls_equal=True,
            )
            .with_columns(earliest=pl.col("caseid").min().over(*CONTEST, "code"))
            .sort("on", "earliest", "caseid")
            .drop("on", "earliest")
            .group_by(*CONTEST, "code", maintain_order=True)
        )
        for keys, group in ordered:
            year, month, day, etype, sab, sen, _, _, _, mmdpost, code = keys
            assert isinstance(sab, str) and isinstance(sen, int) and isinstance(etype, str)
            assert isinstance(year, int) and isinstance(month, int) and (day is None or isinstance(day, int))
            assert isinstance(code, str) and (mmdpost is None or isinstance(mmdpost, str))
            datekey, on, kind = days[(sab, year, month, day, etype)]
            _, label, scope, runoff = classify(etype)
            body, body_name, body_slug = bodies[(sab, sen)]
            area, unit, shown = districts[(sab, sen, code)]
            post = mmdpost if mmdpost is not None and mmdpost.isdigit() else None
            contest_key = f"{datekey}:{sab}:{body_slug}:{code}" + (f":{mmdpost}" if mmdpost else "") + f":{etype}"
            contest = cs.key("us.contest", contest_key)
            outcomes: dict[str, str] = {}
            for candid, outcome in group.select("candid", "outcome").iter_rows():
                if outcome in RANK and RANK[outcome] < RANK.get(outcomes.get(candid, ""), 9):
                    outcomes[candid] = outcome
            won = sum(1 for outcome in outcomes.values() if outcome == "w")
            advanced = sum(1 for outcome in outcomes.values() if outcome == "r")
            first_round = bool(re.search(r"f(settled|unsettled|partunsettled)$", etype)) or etype.startswith("la")
            eseats = group["eseats"][0]
            single = group["dtype"][0] in (1, 2, 4, 5, 7, 8)
            unknown = re.search(r"f(unsettled|partunsettled)$", etype) is not None and advanced == 0
            magnitude = (
                1
                if etype == "ttp" or (single and (first_round or runoff))
                else won + (advanced + 1) // 2
                if first_round and not runoff and not unknown
                else eseats or None
            )
            previous = None
            if runoff:
                base = etype.removesuffix("runoff")
                for other in (f"{base}funsettled", f"{base}fpartunsettled", f"{base}fsettled", base):
                    match = contests.get((sab, sen, code, mmdpost, other, year))
                    if match is not None and match[1] < on:
                        previous = match[0]
                        break
            general = re.sub(r"(fsettled|funsettled|fpartunsettled|runoff)$", "", etype) in ("g", "gs", "ssg")
            blanket = general and (sab == "NE" or (sab == "CA" and year >= 2012) or (sab == "WA" and year >= 2008))
            if (
                blanket
                and (match := contests.get((sab, sen, code, mmdpost, "ttp", year))) is not None
                and match[1] < on
            ):
                previous = match[0]
            if etype == "ttp" or blanket:
                system = "top_two"
            elif sab in ("VT", "NH") and general and (first_round or runoff):
                system = "fptp" if runoff or magnitude == 1 else "block"
            elif (
                first_round
                or runoff
                or (sab == "GA" and "primary" not in label)
                or (
                    "primary" in label
                    and (
                        sab in ("AL", "AR", "GA", "MS", "NC", "OK", "SC", "TX")
                        or (sab == "FL" and year < 2002)
                        or (sab == "LA" and year < 1975)
                        or (sab == "VA" and year <= 1969)
                    )
                )
            ):
                system = (
                    "two_round_40"
                    if sab == "NC"
                    else "two_round_45"
                    if sab == "GA" and general and 1994 <= year <= 2004
                    else "two_round"
                )
            elif group["dtype"][0] == 10:
                system = "cumulative"
            elif magnitude == 1 or (magnitude is None and group["dtype"][0] not in (3, 6, 9)):
                system = "fptp"
            else:
                system = "block"
            if contest in cs.minted or contest in owned:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=elections[(sab, year, month, day, etype)],
                    kind="body_seats",
                    body_id=body,
                    district_area_id=area,
                    magnitude=magnitude,
                    term="remainder" if etype == "gs" else "full",
                    round_number=2 if previous is not None else 1,
                    previous_round_id=previous,
                    electoral_system_id=systems[system],
                    default_result_kind="counted",
                    party_scope_id=party(sab, None, scope)[0] if scope in ("d", "r") else party(sab, scope, "nm")[0],
                )
                where = shown + (f", Post {post}" if post else "")
                name(contest, f"{year} {body_name} {label}, {where}")
                path = (
                    f"us/{sab.lower()}/{datekey}-{kind}/{'state-' if body_slug in ('senate', 'house') else ''}{body_slug}-{code}"
                    + (f"-post-{post}" if post else "")
                    + ("" if label == "election" else f"-{slug(label)}")
                    + (f"-{etype[-1]}" if etype[-1].isdigit() else "")
                )
                cs.add("slug", path=unique(path, contest), entity_id=contest)
            contests[(sab, sen, code, mmdpost, etype, year)] = (contest, on)
            first = cs.key(f"{SOURCE}.result_set", contest_key)
            result_set = (
                cs.key(f"{SOURCE}.result_set", f"{contest_key}:2")
                if first not in cs.minted and group["caseid"].is_in([*CORRECTED, *OUTCOMES]).any()
                else first
            )
            if result_set != first:
                cs.add(
                    "result_set",
                    id=first,
                    election_id=elections[(sab, year, month, day, etype)],
                    contest_id=contest,
                    stage="certified",
                    kind="counted",
                    status="superseded",
                    source_id=source,
                    is_default=False,
                    supersedes_id=None,
                    completeness=None if group["votes"].is_null().any() else 1,
                )
                for option, outcome, count in conn.execute(
                    "select option_id, kind, seats from outcome where result_set_id = %s", [first]
                ).fetchall():
                    cs.add("outcome", result_set_id=first, option_id=option, kind=outcome, seats=count)
            cs.add(
                "result_set",
                id=result_set,
                election_id=elections[(sab, year, month, day, etype)],
                contest_id=contest,
                stage="certified",
                kind="counted",
                status="superseded" if demoted.get(result_set) == "superseded" else "active",
                source_id=source,
                is_default=contest_key not in RANKED_ROUNDS and first not in demoted,
                supersedes_id=None if result_set == first else first,
                completeness=None if group["votes"].is_null().any() else 1,
            )
            fresh = result_set in cs.minted or result_set in zeroed or contest_key in RESTAGED
            nomination = "primary" in label
            for index, (candid, rows) in enumerate(group.group_by("candid", maintain_order=True)):
                row = rows.row(0, named=True)
                scattered = row["cand"] == "scattering"
                write_in = scattered or all(value == "w" for value in rows["partyz"])
                option_key = f"{contest_key}:scattering:{index}" if scattered else f"{contest_key}:{candid[0]}"
                root = cs.key("us.option", option_key)
                labels = [(label, partyz) for label, partyz in rows.select("party", "partyz").iter_rows()]
                resolved = [party(sab, label, partyz) for label, partyz in labels]
                ballot = "Scattered write-ins" if scattered else person_name(row)
                majors = {id for id, _ in resolved if id in principals}
                named = [(votes or 0, id) for (id, _), votes in zip(resolved, rows["votes"], strict=True) if id]
                principal = (
                    resolved[0][0]
                    if len(rows) == 1
                    else next(iter(majors))
                    if len(majors) == 1
                    else max(named)[1]
                    if named
                    else None
                )
                if root in cs.minted or root in published:
                    cs.add(
                        "contest_option",
                        id=root,
                        contest_id=contest,
                        kind="write_in" if write_in else "candidate",
                        party_id=published.get(root) or principal if len(rows) > 1 else published.get(root, principal),
                        ballot_label=ballot,
                    )
                if not scattered and (
                    (candidacy := cs.key("us.candidacy", f"{option_key}:head")) in cs.minted or candidacy in owned
                ):
                    cs.add(
                        "candidacy",
                        id=candidacy,
                        person_id=persons[str(candid[0])],
                        option_id=root,
                        role="head",
                        ballot_name=ballot,
                        is_independent=all(independent for _, independent in resolved),
                    )
                for (line_label, partyz), (party_id, _), votes in zip(labels, resolved, rows["votes"], strict=True):
                    target = root
                    if len(rows) > 1:
                        text = line_label or {"d": "democrat", "r": "republican", "w": "write-in"}.get(partyz, partyz)
                        if partyz == "w" and line_label is not None and "writein" not in line_label:
                            text += " write-in"
                        target = cs.key("us.option", f"{option_key}:{slug(text)}")
                        if target in cs.minted or target in published:
                            cs.add(
                                "contest_option",
                                id=target,
                                contest_id=contest,
                                parent_option_id=root,
                                kind="write_in" if partyz == "w" else "candidate",
                                party_id=published.get(target, party_id),
                                ballot_label=f"{ballot} ({text})",
                            )
                    if fresh and votes is not None:
                        values.append((str(result_set), str(unit), str(target), votes))
                outcome = outcomes.get(str(candid[0]))
                if outcome is not None and not scattered and not unknown:
                    kind_ = (
                        "tied"
                        if outcome == "t"
                        else "withdrew"
                        if outcome == "withdrew"
                        else "advanced"
                        if outcome == "r"
                        or etype == "ttp"
                        or (outcome == "w" and contest in chained and not etype.endswith("fpartunsettled"))
                        else "won"
                        if nomination
                        else "elected"
                    )
                    cs.add(
                        "outcome",
                        result_set_id=result_set,
                        option_id=root,
                        kind=kind_,
                        seats=1 if kind_ == "elected" else None,
                        notes=WITHDREW.get(row["caseid"]) if kind_ == "withdrew" else None,
                    )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            ).with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        for (scheme, value), reason in RETIRED.items():
            if (
                value.split(":")[1] in chunk.split(",")
                and not (
                    scheme == "us.option"
                    and conn.execute("select 1 from candidacy where option_id = %s", [cs.key(scheme, value)]).fetchone()
                )
                and (scheme, value)
                in conn.execute(
                    """select x.scheme, x.value from external_id x join entity e on e.id = x.entity_id
                   where x.scheme = %s and x.value = %s and e.retired_by is null""",
                    [scheme, value],
                ).fetchall()
            ):
                cs.add("entity_retirement", entity_id=cs.key(scheme, value), reason=reason)
        held: dict[UUID, list[tuple[UUID, str, str, bool]]] = {}
        for id, entity, text, kind, ours in conn.execute(
            """select n.id, n.entity_id, n.text, n.kind::text, c.pipeline is not distinct from %s
               from name n left join changeset c on c.id = n.changeset_id
               where n.entity_id = any(%s) and n.is_preferred and n.lang = 'en'
                 and n.valid_from is null and n.valid_to is null""",
            [SOURCE, [entity for entity in staged_names if entity not in cs.minted]],
        ).fetchall():
            held.setdefault(entity, []).append((id, text, kind, ours))
        for entity, text in staged_names.items():
            foreign = any(not ours for *_, ours in held.get(entity, []))
            if not foreign or text not in {held_text for _, held_text, *_ in held[entity]}:
                cs.add(
                    "name", id=None, entity_id=entity, text=text, lang="en", kind="official", is_preferred=not foreign
                )
            kept = False
            for id, old, kind, ours in held.get(entity, []):
                if ours and (foreign or old != text or kept):
                    cs.add("name", id=id, entity_id=entity, text=old, lang="en", kind=kind, is_preferred=False)
                kept = kept or (ours and old == text)
        return cs.write()
