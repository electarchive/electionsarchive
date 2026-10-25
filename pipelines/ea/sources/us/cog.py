import io
import re
import subprocess
import zipfile
from collections import defaultdict
from xml.etree import ElementTree

import fastexcel
import polars as pl

from ea import raw
from ea.coverage_tables import COLLEGE, PREFIX, tokens
from ea.store import DATA

SOURCE = "us.cog"
URL = "https://www2.census.gov/programs-surveys/gus/datasets/2022/govt_units_2022.ZIP"
SHEETS = {
    "General Purpose": None,
    "Special District": "special",
    "School District": "school",
    "DEP School Dist": "dependent_school",
}
TYPES = {"1 - COUNTY": "county", "2 - MUNICIPAL": "municipality", "3 - TOWNSHIP": "township"}
ISD = "https://www.census.gov/content/dam/Census/library/publications/2024/econ/2022ISD.pdf"
SAIPE = "https://www2.census.gov/programs-surveys/saipe/datasets/2022/2022-school-districts/ussd22.xls"
NUMBERING = {"R", "C", "RE", "UF", "CUSD", "UNION", "FREE", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X"}
SCHOOL_WORDS = {
    "CITY",
    "LOCAL",
    "COMMUNITY",
    "BORO",
    "BOROUGH",
    "TOWN",
    "TOWNSHIP",
    "ELEMENTARY",
    "HIGH",
    "UNIFIED",
    "UNIT",
    "CONSOLIDATED",
    "INDEPENDENT",
    "JOINT",
    "REGIONAL",
    "BOARD",
    "EDUCATION",
    "PUB",
    "COOP",
    "COOPERATIVE",
    "AREA",
    "EXEMPTED",
    "VILLAGE",
    "VLG",
    "EX",
    "RURAL",
    "REORGANIZED",
    "REORG",
    "ADMIN",
    "ADMINISTRATIVE",
    "DEPARTMENT",
    "CENTRAL",
    "COUNTY",
}
STATES = {
    "Alabama": "01",
    "Alaska": "02",
    "Arizona": "04",
    "Arkansas": "05",
    "California": "06",
    "Colorado": "08",
    "Connecticut": "09",
    "Delaware": "10",
    "District of Columbia": "11",
    "Florida": "12",
    "Georgia": "13",
    "Hawaii": "15",
    "Idaho": "16",
    "Illinois": "17",
    "Indiana": "18",
    "Iowa": "19",
    "Kansas": "20",
    "Kentucky": "21",
    "Louisiana": "22",
    "Maine": "23",
    "Maryland": "24",
    "Massachusetts": "25",
    "Michigan": "26",
    "Minnesota": "27",
    "Mississippi": "28",
    "Missouri": "29",
    "Montana": "30",
    "Nebraska": "31",
    "Nevada": "32",
    "New Hampshire": "33",
    "New Jersey": "34",
    "New Mexico": "35",
    "New York": "36",
    "North Carolina": "37",
    "North Dakota": "38",
    "Ohio": "39",
    "Oklahoma": "40",
    "Oregon": "41",
    "Pennsylvania": "42",
    "Rhode Island": "44",
    "South Carolina": "45",
    "South Dakota": "46",
    "Tennessee": "47",
    "Texas": "48",
    "Utah": "49",
    "Vermont": "50",
    "Virginia": "51",
    "Washington": "53",
    "West Virginia": "54",
    "Wisconsin": "55",
    "Wyoming": "56",
}
FOOTER = re.compile(rf"^(?:({'|'.join(STATES)}) \d+|\d+ ({'|'.join(STATES)}))$")
ELECTED = re.compile(
    r"\b(popularly )?elected\b(?! (officials?|officers?|bod(y|ies)|county|city|town|state|governing|representatives?|"
    r"heads?|members? of (the|a|each) (county|city|town|municipal|participating|member)))",
    re.IGNORECASE,
)
STOP = {"DISTRICT", "OF", "AND", "OR", "THE", "FOR", "LAW", "ACT", "COUNTY", "IN", "A"}
GENERIC = {
    "AUTHORITY",
    "COMMISSION",
    "BOARD",
    "AGENCY",
    "CORPORATION",
    "JOINT",
    "SPECIAL",
    "PUBLIC",
    "OR",
    "CITY",
    "TOWN",
    "TOWNSHIP",
    "MUNICIPAL",
    "REGIONAL",
    "LOCAL",
    "CREATED",
    "UNDER",
} | {word.upper() for state in STATES for word in state.split()}
APPOINTED_COLLEGES = {
    "11": "D.C. Code § 38-1202.01(c), https://code.dccouncil.gov/us/dc/council/code/sections/38-1202.01",
    "12": "Fla. Stat. § 1001.61(2), https://www.flsenate.gov/Laws/Statutes/2025/1001.61",
    "24": "Md. Code, Educ. § 16-101(c), https://mgaleg.maryland.gov/2024RS/Statute_Web/ged/16-101.pdf",
    "28": "Miss. Code § 37-29-65(6), https://codes.findlaw.com/ms/title-37-education/ms-code-sect-37-29-65/",
    "34": "N.J. Stat. § 18A:64A-8, https://law.onecle.com/new-jersey/title-18a/18a-64a-8.html",
    "36": "N.Y. Educ. Law § 6306(1), https://www.nysenate.gov/legislation/laws/EDN/6306",
    "37": "N.C. Gen. Stat. § 115D-12(a), https://www.ncleg.gov/EnactedLegislation/Statutes/HTML/BySection/Chapter_115D/GS_115D-12.html",
    "39": "Ohio Rev. Code § 3354.05, https://codes.ohio.gov/ohio-revised-code/section-3354.05",
    "42": "24 P.S. § 19-1904-A(a), https://www.palegis.us/statutes/unconsolidated/law-information/view-statute?act=014&chpt=19A&sessInd=0&smthLwInd=0&txtType=HTM&yr=1949",
    "55": "Wis. Stat. § 38.08, https://docs.legis.wisconsin.gov/statutes/statutes/38/08",
}
COUNTS = {"county": 3031, "municipality": 19491, "township": 16214, "special": 39555, "school": 12546}


def fetch() -> None:
    raw.fetch(SOURCE, URL, suffix=".zip")
    raw.fetch(SOURCE, ISD, suffix=".pdf")
    raw.fetch(SOURCE, SAIPE, suffix=".xls")


def words(text: str) -> frozenset[str]:
    return frozenset(
        w[:-3] + "Y" if w.endswith("IES") else w.rstrip("S")
        for w in re.sub(r"\(.*?\)|—.*|[^A-Z ]", " ", text.upper()).split()
    )


def boards() -> dict[str, list[tuple[frozenset[str], str | None]]]:
    root = ElementTree.fromstring(
        subprocess.run(
            ["pdftohtml", "-xml", "-i", "-q", "-stdout", str(raw.cached(SOURCE, ISD).path)],
            capture_output=True,
            check=True,
        ).stdout
    )
    blocks: list[list] = []
    inside = False
    state = None
    for page in root.iter("page"):
        items = []
        for text in page.iter("text"):
            content = "".join(text.itertext()).strip()
            if footer := FOOTER.match(re.sub(r"\s+", " ", content)):
                state = STATES[footer[1] or footer[2]]
            elif content and int(text.get("top")) <= 1110:
                items.append((int(text.get("left")) >= 459, int(text.get("top")), text.find("b") is not None, content))
        for _, _, bold, content in sorted(items):
            if bold and re.fullmatch(r"SPECIAL DISTRICT GOVERNMENTS\d?", content):
                inside = True
            elif bold and re.fullmatch(r"[A-Z][A-Z,—-]*(?: [A-Z,—-]+)+", content):
                inside = False
            elif (
                inside
                and bold
                and blocks
                and blocks[-1][0] == state
                and (not blocks[-1][2] or blocks[-1][1].endswith("-"))
            ):
                blocks[-1][1] = blocks[-1][1].removesuffix("-") + ("" if blocks[-1][1].endswith("-") else " ") + content
            elif inside and bold:
                blocks.append([state, content, []])
            elif inside and blocks:
                blocks[-1][2].append(content)
    classes = []
    for state, heading, para in blocks:
        para = re.sub(r"-\s", "", " ".join(para))
        board = "elected" if ELECTED.search(para) else "appointed" if "appoint" in para.lower() else None
        classes.append((state, words(heading) - STOP, board))
    for i, (state, heading, board) in enumerate(classes):
        children = [c[2] for c in classes[i + 1 : i + 8] if c[0] == state and c[2] is not None and heading < c[1]]
        if board is None and children:
            classes[i] = (state, heading, "elected" if "elected" in children else "appointed")
    by_state: dict[str, list[tuple[frozenset[str], str | None]]] = {}
    for state, heading, board in classes:
        by_state.setdefault(state, []).append((heading, board))
    return by_state


def parse() -> None:
    book = fastexcel.read_excel(
        zipfile.ZipFile(io.BytesIO(raw.cached(SOURCE, URL).path.read_bytes())).read("Govt_Units_2022_Final.xlsx")
    )
    frames = []
    for sheet, kind in SHEETS.items():
        frame = book.load_sheet_by_name(sheet, dtypes="string").to_polars()
        population = next((c for c in ("POPULATION", "ENROLLMENT") if c in frame.columns), None)
        frames.append(
            frame.select(
                pl.coalesce("CENSUS_ID_GIDID", pl.lit("pid6:") + pl.col("CENSUS_ID_PID6")).alias("id"),
                (pl.col("UNIT_TYPE").replace_strict(TYPES) if kind is None else pl.lit(kind)).alias("kind"),
                pl.col("UNIT_NAME").str.strip_chars().alias("name"),
                pl.col("FIPS_STATE").alias("state_fips"),
                pl.col("FIPS_COUNTY").alias("county_fips"),
                (pl.col("FIPS_PLACE") if "FIPS_PLACE" in frame.columns else pl.lit(None, pl.String)).alias(
                    "place_fips"
                ),
                (pl.col("FUNCTION_NAME") if "FUNCTION_NAME" in frame.columns else pl.lit(None, pl.String)).alias(
                    "function"
                ),
                (pl.lit(None) if population is None else pl.col(population).cast(pl.Float64))
                .cast(pl.Int64, strict=False)
                .alias("population"),
                (pl.col("IS_ACTIVE") == "Y").alias("active"),
            )
        )
    governments = pl.concat(frames)
    if governments["id"].n_unique() != governments.height:
        raise ValueError("duplicate government IDs")
    classes = boards()
    matched = []
    for gid, name, st in governments.filter(pl.col("kind") == "special").select("id", "name", "state_fips").iter_rows():
        mine = words(name)
        ranked = [
            ((len(heading & mine) / len(heading), len(heading & mine), board is not None), board)
            for heading, board in classes.get(st, [])
            if heading and heading - GENERIC & mine and len(heading & mine) / len(heading) >= 0.5
        ]
        matched.append((gid, max(ranked, key=lambda r: r[0])[1] if ranked else None))
    governments = governments.join(pl.DataFrame(matched, schema=["id", "board"], orient="row"), on="id", how="left")
    for keys in (["state_fips", "function"], ["function"]):
        share = (
            governments.filter(pl.col("board").is_not_null())
            .group_by(keys)
            .agg((pl.col("board") == "elected").mean().alias("share"))
        )
        governments = (
            governments.join(share, on=keys, how="left")
            .with_columns(
                pl.when(pl.col("kind") != "special")
                .then(None)
                .otherwise(
                    pl.coalesce(
                        "board",
                        pl.when(pl.col("share") >= 0.5)
                        .then(pl.lit("elected"))
                        .when(pl.col("share").is_not_null())
                        .then(pl.lit("appointed")),
                    )
                )
                .alias("board")
            )
            .drop("share")
        )
    governments = governments.with_columns(
        (
            (pl.col("board") != "appointed").fill_null(True)
            & ~(
                pl.col("kind").is_in(["school", "dependent_school"])
                & pl.col("name").map_elements(lambda name: COLLEGE.search(name) is not None, return_dtype=pl.Boolean)
                & pl.col("state_fips").is_in(list(APPOINTED_COLLEGES))
            )
        ).alias("elected")
    ).drop("board")
    held = dict(governments.group_by("kind").len().iter_rows())
    for kind, count in COUNTS.items():
        if held.get(kind) != count:
            raise ValueError(f"{kind}: {held.get(kind)} units, the Census Bureau reports {count}")
    saipe = (
        fastexcel.read_excel(raw.cached(SOURCE, SAIPE).path.read_bytes())
        .load_sheet(0, header_row=2, dtypes="string")
        .to_polars()
    )
    index: list[dict[tuple[str, object], list[int]]] = [defaultdict(list) for _ in range(4)]
    schools: list[tuple[str, str, tuple[object, ...]]] = []
    for gid, st, name, population in [
        (None, st, name, int(population))
        for st, name, population in saipe.select("State FIPS Code", "Name", "Estimated Total Population").iter_rows()
    ] + [
        (gid, st, name, 0)
        for gid, st, name in governments.filter(pl.col("kind").is_in(["school", "dependent_school"]))
        .select("id", "state_fips", "name")
        .iter_rows()
    ]:
        exact = tokens(name)
        plain = frozenset(w for w in exact if not any(c.isdigit() for c in w) and w not in NUMBERING)
        keys = (exact, plain, "".join(sorted(re.sub(r"[^A-Z]", "", w) for w in plain)), plain - SCHOOL_WORDS)
        if gid is None:
            for i, key in enumerate(keys):
                index[i][(st, key)].append(population)
        else:
            schools.append((gid, st, keys))
    figures: dict[str, tuple[int, str]] = {}
    for gid, st, keys in schools:
        hits = next((hits for i, key in enumerate(keys) if len(hits := index[i].get((st, key), [])) == 1), None)
        if hits is not None:
            figures[gid] = (hits[0], "saipe")
    counties: dict[tuple[str, str], int] = {}
    places: dict[tuple[str, str], list[tuple[str, int]]] = defaultdict(list)
    for kind, name, st, county, population in (
        governments.filter(pl.col("kind").is_in(["county", "municipality", "township"]))
        .select("kind", "name", "state_fips", "county_fips", "population")
        .iter_rows()
    ):
        if kind == "county":
            counties[(st, county)] = population
        elif population:
            match = PREFIX.match(name)
            places[(st, county)].append(((name if match is None else match[2]).strip(), population))
    covered: dict[tuple[str, str], int] = defaultdict(int)
    for gid, name, st, county in (
        governments.filter(pl.col("kind").is_in(["school", "dependent_school"]))
        .select("id", "name", "state_fips", "county_fips")
        .iter_rows()
    ):
        if gid in figures and not (COLLEGE.search(name) or "TECHNICAL" in name):
            covered[(st, county)] += figures[gid][0]
    sharing: dict[tuple[str, str, str, str | None, str], list[str]] = defaultdict(list)
    for gid, kind, name, st, county in (
        governments.filter(pl.col("kind").is_in(["special", "school", "dependent_school"]))
        .select("id", "kind", "name", "state_fips", "county_fips")
        .iter_rows()
    ):
        if gid in figures:
            continue
        place = max(
            (
                (prefix, population)
                for prefix, population in places.get((st, county), [])
                if kind == "special"
                and name.startswith(prefix + " ")
                and name[len(prefix) + 1 :].split(" ", 1)[0] not in ("COUNTY", "PARISH")
            ),
            key=lambda pair: len(pair[0]),
            default=None,
        )
        group = (
            "special" if kind == "special" else "college" if COLLEGE.search(name) or "TECHNICAL" in name else "school"
        )
        if place is not None:
            sharing[("place", st, county, place[0], group)].append(gid)
            figures[gid] = (place[1], "place")
        elif (st, county) in counties:
            sharing[("county", st, county, None, group)].append(gid)
            figures[gid] = (
                max(0, counties[(st, county)] - (covered[(st, county)] if group == "school" else 0)),
                "county",
            )
    for gids in sharing.values():
        for gid in gids:
            figures[gid] = (figures[gid][0] // len(gids), figures[gid][1])
    governments = (
        governments.with_columns(
            pl.col("id")
            .replace_strict({gid: figure[0] for gid, figure in figures.items()}, default=None)
            .alias("figure"),
            pl.col("id")
            .replace_strict({gid: figure[1] for gid, figure in figures.items()}, default=None)
            .alias("population_basis"),
        )
        .with_columns(
            pl.when(pl.col("kind").is_in(["county", "municipality", "township"]))
            .then(pl.col("population"))
            .otherwise(pl.col("figure"))
            .cast(pl.Int64)
            .alias("population"),
            pl.when(pl.col("kind").is_in(["county", "municipality", "township"]))
            .then(pl.lit("census"))
            .otherwise(pl.col("population_basis"))
            .alias("population_basis"),
        )
        .drop("figure")
    )
    print(
        governments.group_by("kind", "population_basis")
        .agg(pl.len().alias("governments"), pl.col("population").sum().alias("population"))
        .sort("kind", "population_basis")
    )
    out = DATA / "parsed" / SOURCE
    out.mkdir(parents=True, exist_ok=True)
    governments.write_parquet(out / "governments.parquet")
