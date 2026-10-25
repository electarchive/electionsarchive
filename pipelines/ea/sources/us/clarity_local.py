import io
import json
import re
import zipfile
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from pathlib import Path
from uuid import UUID
from xml.etree import ElementTree

import polars as pl
import shapefile

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.enr_clarity import _title
from ea.store import DATA, connect

SOURCE = "us.clarity_local"
VERSION = "2"
PARSED = DATA / "parsed" / SOURCE
CLARITY = "https://results.enr.clarityelections.com/{state}/{county}/{election}/{version}/reports/detailxml.zip"
TITLE = "Election night reporting results"
STATES = {"17": "IL", "20": "KS", "26": "MI", "31": "NE", "39": "OH", "42": "PA"}
STATE_NAMES = {"IL": "Illinois", "KS": "Kansas", "MI": "Michigan", "NE": "Nebraska", "OH": "Ohio", "PA": "Pennsylvania"}
COUNTIES = {
    "17043": (
        "DuPage",
        "DuPage County Clerk",
        {
            123485: 357747,
            122435: 355809,
            117398: 317563,
            115972: 314432,
            108904: 276677,
            106122: 272285,
            94959: 226920,
            76496: 222861,
        },
    ),
    "17111": (
        "McHenry",
        "McHenry County Clerk",
        {
            123537: 357757,
            122427: 355800,
            117556: 317568,
            115977: 314530,
            109015: 276674,
            105201: 272049,
            94965: 226845,
            91636: 224882,
            64179: 227001,
            55315: 152868,
        },
    ),
    "17117": (
        "Macoupin",
        "Macoupin County Clerk",
        {
            123244: 357587,
            122495: 355694,
            117559: 317501,
            115970: 316207,
            108895: 276583,
            95360: 226884,
            63908: 184147,
            55300: 151545,
            53474: 149378,
            46043: 116560,
            43958: 113348,
        },
    ),
    "17201": (
        "Winnebago",
        "Winnebago County Clerk",
        {
            66446: 185983,
            64406: 184317,
            45704: 116545,
            43746: 114258,
            28913: 43630,
            22910: 40319,
            10445: 15504,
            8819: 13894,
        },
    ),
    "17119": ("Madison", "Madison County Clerk", {123379: 357742, 122423: 355592}),
    "39061": (
        "Hamilton",
        "Hamilton County Board of Elections",
        {
            124347: 369495,
            122586: 355519,
            119018: 325029,
            118315: 319153,
            115759: 314263,
            110775: 285026,
            105202: 275590,
            104650: 258598,
            98137: 235991,
            96256: 229191,
            77124: 224797,
            76498: 210156,
            71592: 191595,
            64172: 184023,
            63066: 174751,
            57508: 157338,
            56404: 153773,
            53689: 149619,
            52363: 138542,
            49399: 123481,
            47339: 117919,
        },
    ),
    "26055": (
        "Grand_Traverse",
        "Grand Traverse County Clerk",
        {
            124296: 366144,
            124298: 366037,
            123827: 358343,
            122755: 355585,
            118577: 324427,
            115411: 313599,
            112975: 287931,
            111523: 284483,
        },
    ),
    "26139": (
        "Ottawa",
        "Ottawa County Clerk",
        {126063: 371710, 124187: 366657, 123555: 358719, 123542: 357881, 123248: 357882},
    ),
    "26143": (
        "Roscommon",
        "Roscommon County Clerk",
        {
            126065: 371769,
            124475: 364900,
            123553: 358339,
            122490: 355757,
            118573: 323932,
            115741: 317033,
            109971: 278049,
            103292: 247335,
            96147: 229154,
        },
    ),
    "42115": (
        "Susquehanna",
        "Susquehanna County Bureau of Elections",
        {122485: 354242, 119035: 323586, 116168: 310875, 111521: 283496},
    ),
    "42051": ("Fayette", "Fayette County Election Bureau", {125144: 367166, 122836: 355870}),
    "42133": ("York", "York County Office of Elections", {119017: 369102}),
    "20079": (
        "Harvey",
        "Harvey County Clerk",
        {
            125935: 368735,
            124688: 367253,
            122758: 365748,
            118958: 367104,
            115750: 367119,
            111055: 367116,
            106041: 367115,
            98749: 367121,
            92669: 367249,
            71590: 367122,
            67626: 367285,
            64160: 367286,
        },
    ),
    "20121": (
        "Miami",
        "Miami County Clerk",
        {
            124189: 366480,
            123552: 359181,
            123538: 358379,
            122433: 357589,
            118629: 324767,
            115742: 314015,
            115407: 301566,
            110768: 284790,
            106205: 281487,
            95989: 235896,
            91800: 222046,
            71302: 191263,
            69895: 187892,
            59146: 183367,
            59144: 163982,
            55468: 151970,
            55189: 281437,
            54886: 150788,
            50886: 148837,
            50884: 125463,
            43029: 112464,
            45272: 114401,
            41794: 100464,
        },
    ),
    "31153": (
        "Sarpy",
        "Sarpy County Election Commission",
        {
            125926: 368264,
            123023: 356163,
            122854: 355957,
            117800: 318363,
            116155: 314091,
            115136: 300257,
            107143: 271967,
            92897: 237043,
            64652: 237038,
            54217: 237036,
            43953: 237446,
        },
    ),
}
DATES = {
    105201: date(2020, 11, 3),
    109015: date(2021, 4, 6),
    49399: date(2013, 11, 5),
    104650: date(2020, 8, 4),
    96256: date(2019, 8, 6),
    10445: date(2009, 4, 7),
    8819: date(2008, 11, 4),
}
RENAMED = {
    "2009-04-07:IL": "0001-01-01:IL",
    **{
        f"2008-11-04:IL:{code}:question": f"0001-01-01:IL:{code}:question"
        for code in ("1782491", "1720168783", "1720133188")
    },
}
SUBDIVISIONS = {st: f"https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_{st}_cousub_500k.zip" for st in STATES}
PLACES = {st: f"https://www2.census.gov/geo/tiger/GENZ2020/shp/cb_2020_{st}_place_500k.zip" for st in STATES}
SKIPPED = re.compile(
    r"SCHOOL|EDUCATION|\bSD\b|\bCUSD\b|\bUD\b|LIBRARY|PARK DIST|PARK COMM|PARK BOARD|\bPARKS\b|FIRE PROTECTION|"
    r"FIRE DIST|SANITARY|COURT|JUDGE|COUNTY|CONGRESS|\bSTATE\b|\bSENAT|REPRESENTATIVE|GOVERNOR|COMMITTEE|DELEGATE|"
    r"CONSTABLE|INSPECTOR|FOREST PRESERVE|CONSERVATION|AIRPORT|HOSPITAL|MOSQUITO|CEMETERY|DRAINAGE|RECLAMATION|"
    r"COLLEGE|UNIVERSITY|LIGHT AND POWER|CIRCUIT|MULTI|LOCAL OPTION|PRESIDENT AND|UNITED STATES|\bU S\b|\bESR\b|"
    r"REGIONAL|AMBULANCE|\bEMS\b|PENSION|MENTAL|HEALTH|AUTHORITY|TRANSIT|\bZOO\b|MUSEUM|\bSOIL\b|PRECINCT|"
    r"ELECTION|\bWATER\b|SEWER|RESCUE|RECREATION|\bLIB\b|\bFPD\b|\bPK DIST|FIRE (?:\d+ )?PROP"
)
QUESTION = re.compile(
    r"PROPOSITION|PROPOSAL|REFERENDUM|QUESTION|LEVY|ISSUE|MILLAGE|ORDINANCE|ADVISORY|BOND|CHARTER|\bTAX\b(?! COLLECTOR)"
)
TOWNSHIP_KEYS = (
    "trustee",
    "supervisor",
    "highway-commissioner",
    "assessor",
    "collector",
    "township-board",
    "fiscal-officer",
)
PLACE_KEYS = ("mayor", "president", "council", "council-president", "board-of-trustees", "law-director")
BODIES = (
    "council",
    "board-of-trustees",
    "township-board",
    "board-of-supervisors",
    "board-of-commissioners",
    "auditors",
)
NUMBERS = {"ONE": "1", "TWO": "2", "THREE": "3", "FOUR": "4", "FIVE": "5", "SIX": "6"}
PARTIES = {"DEM": "democratic-party", "REP": "republican-party"}
METHOD = (
    "Votes are the {publisher}'s election night reporting results by precinct, summed across the ways of voting the "
    "county reports. Every candidate's precinct votes add up to the county's total."
)
WITHDRAWN = (
    "Withdrawn: an earlier reading of the county's results put this race under the wrong contest, such as a term "
    "length read as a seat or a fire district's question read as the town's; the corrected reading loads the race, "
    "where it is the town's, under its own contest."
)
TOTALS = (
    "Votes are the {publisher}'s election night reporting results, counted as one total for the whole jurisdiction "
    "because the county's precincts do not add up to its total or are already in the archive under other names."
)


def url(county: str, election: int, version: int) -> str:
    return CLARITY.format(state=STATES[county[:2]], county=COUNTIES[county][0], election=election, version=version)


def fetch() -> None:
    for county, (_, _, elections) in COUNTIES.items():
        for election, version in elections.items():
            raw.fetch(SOURCE, url(county, election, version), suffix=".zip")
    for link in (*SUBDIVISIONS.values(), *PLACES.values()):
        raw.fetch(SOURCE, link, suffix=".zip")


def _shapes(link: str) -> list[tuple[Mapping[str, object], str, tuple[float, ...]]]:
    with zipfile.ZipFile(raw.cached(SOURCE, link).path) as archive:
        parts = {name.rsplit(".", 1)[1].lower(): archive.read(name) for name in archive.namelist()}
    reader = shapefile.Reader(shp=io.BytesIO(parts["shp"]), shx=io.BytesIO(parts["shx"]), dbf=io.BytesIO(parts["dbf"]))
    return [
        (record.as_dict(), json.dumps(shape.__geo_interface__), tuple(shape.bbox))
        for record, shape in zip(reader.iterRecords(), reader.iterShapes(), strict=True)
        if record is not None and shape is not None
    ]


def _plain(text: str) -> str:
    return re.sub(r"\s+", " ", re.sub(r"[^A-Z0-9]+", " ", text.upper())).strip()


def parse() -> None:
    munis: dict[str, list[tuple[str, str, str, str, str]]] = {}
    shapes: dict[tuple[str, str], tuple[str, str]] = {}
    boxes: dict[str, list[float]] = {}
    for st in STATES:
        for record, geom, box in _shapes(SUBDIVISIONS[st]):
            county = str(record["GEOID"])[:5]
            if county not in COUNTIES or record["COUSUBFP"] == "00000":
                continue
            found = boxes.setdefault(county, list(box))
            boxes[county] = [min(found[0], box[0]), min(found[1], box[1]), max(found[2], box[2]), max(found[3], box[3])]
            proper, whole = str(record["NAME"]), str(record["NAMELSAD"])
            kind = whole[len(proper) :].strip().lower()
            if kind in ("township", "charter township"):
                display = f"{proper} {'Charter Township' if 'charter' in kind else 'Township'}"
                kind = "township"
            elif kind in ("borough", "town"):
                display = proper
            else:
                continue
            code = str(record["GEOID"])
            munis.setdefault(county, []).append((_plain(proper), "us.town", code, display, kind))
            shapes[("us.town", code)] = (display, geom)
        for record, geom, box in _shapes(PLACES[st]):
            lsad = str(record["LSAD"])
            if lsad not in ("21", "25", "43", "47"):
                continue
            kind = {"21": "borough", "25": "city", "43": "town", "47": "village"}[lsad]
            code = str(record["GEOID"])
            for county in COUNTIES:
                if county[:2] != st or county not in boxes:
                    continue
                edge = boxes[county]
                if box[0] > edge[2] or box[2] < edge[0] or box[1] > edge[3] or box[3] < edge[1]:
                    continue
                listed = munis.setdefault(county, [])
                bare = _plain(str(record["NAME"]))
                listed[:] = [entry for entry in listed if not (entry[0] == bare and entry[4] == kind)]
                listed.append((bare, "us.place", code, str(record["NAME"]), kind))
                shapes[("us.place", code)] = (str(record["NAME"]), geom)
    contests, lines = [], []
    for county, (_, publisher, elections) in COUNTIES.items():
        st = STATES[county[:2]]
        listed = munis[county]
        names = sorted({entry[0] for entry in listed}, key=len, reverse=True)
        for election, version in elections.items():
            with zipfile.ZipFile(raw.cached(SOURCE, url(county, election, version)).path) as archive:
                root = ElementTree.fromstring(archive.read("detail.xml"))
            day = DATES.get(election) or datetime.strptime(root.findtext("ElectionDate") or "", "%m/%d/%Y").date()
            title = root.findtext("ElectionName") or ""
            if "PRIMARY" in title.upper():
                continue
            for node in root.iter("Contest"):
                text = re.sub(r"\s+", " ", node.get("text") or "").strip()
                plain = _plain(text)
                if SKIPPED.search(plain):
                    continue
                choices = list(node.iter("Choice"))
                if not choices or any(not (choice.get("totalVotes") or "0").isdigit() for choice in choices):
                    continue
                question = QUESTION.search(plain) is not None
                if question != all(
                    re.match(r"(?i)\s*(?:yes|no|for|against)\b", choice.get("text") or "") for choice in choices
                ):
                    continue
                township_word = re.search(r"\bTOWNSHIP\b|\bTWP\b", plain) is not None
                if question:
                    key = "question"
                elif "PRESIDENT" in plain and "COUNCIL" in plain:
                    key = "council-president"
                elif "PRESIDENT" in plain:
                    key = "president"
                elif re.search(r"\bMAYOR\b", plain):
                    key = "mayor"
                elif re.search(r"\b(?:HIGHWAY|HWY|ROAD) COMM", plain):
                    key = "highway-commissioner"
                elif "SUPERVIS" in plain:
                    key = "board-of-supervisors" if st == "PA" else "supervisor"
                elif "ASSESSOR" in plain:
                    key = "assessor"
                elif "COLLECTOR" in plain:
                    key = "collector"
                elif "FISCAL OFFICER" in plain:
                    key = "fiscal-officer"
                elif "LAW DIRECTOR" in plain:
                    key = "law-director"
                elif re.search(r"\bALDER|COUNCIL|\bCOMMISSIONER\b", plain):
                    key = "council"
                elif re.search(r"\bTRUSTEE|\bTRST\b", plain):
                    key = "trustee" if st == "KS" else "township-board" if township_word else "board-of-trustees"
                elif "CLERK" in plain:
                    key = "clerk-treasurer" if "TREASURER" in plain else "clerk"
                elif "TREASURER" in plain:
                    key = "treasurer"
                elif "AUDITOR" in plain:
                    key = "auditors" if st == "PA" else "auditor"
                else:
                    continue
                match = next((name for name in names if re.search(rf"\b{re.escape(name)}\b", plain)), None)
                if match is None:
                    continue
                candidates = [entry for entry in listed if entry[0] == match]
                after = re.escape(match)
                wanted = None
                if re.search(rf"\b{after} (?:CHARTER )?(?:TOWNSHIP|TWP)\b|\bTOWNSHIP OF {after}\b", plain):
                    wanted = "township"
                elif re.search(rf"\bCITY OF {after}\b|\b{after} CITY\b", plain):
                    wanted = "city"
                elif re.search(rf"\bVILLAGE OF {after}\b|\b{after} VILLAGE\b", plain):
                    wanted = "village"
                elif re.search(rf"\bBOROUGH OF {after}\b|\b{after} (?:BOROUGH|BORO)\b", plain):
                    wanted = "borough"
                elif re.search(rf"\bTOWN OF {after}\b|\b{after} TOWN\b", plain):
                    wanted = "town"
                if wanted is not None:
                    candidates = [entry for entry in candidates if entry[4] == wanted]
                elif key in TOWNSHIP_KEYS:
                    candidates = [entry for entry in candidates if entry[4] == "township"]
                elif key in PLACE_KEYS:
                    candidates = [entry for entry in candidates if entry[4] != "township"]
                if len(candidates) != 1:
                    continue
                _, scheme, code, _, kind = candidates[0]
                if key == "board-of-trustees" and kind == "township":
                    key = "township-board"
                if key == "council" and kind == "township":
                    key = "board-of-commissioners"
                if key in PLACE_KEYS and kind == "township" and key != "board-of-trustees":
                    continue
                seat = None
                if not question:
                    ward = re.search(r"\bWARD (\d+)\b|\b(\d+)(?:ST|ND|RD|TH)? WARD\b|\bWD ?(\d+)\b", plain)
                    ward = ward or re.search(rf"\b{after} (?:BOROUGH |BORO |TOWNSHIP |TWP |CITY )?(\d+)\b", plain)
                    district = re.search(r"\bDISTRICT (\d+|[A-Z])\b|\b(\d+)(?:ST|ND|RD|TH)? DISTRICT\b", plain)
                    if ward:
                        seat = f"Ward {int(next(group for group in ward.groups() if group))}"
                    elif district:
                        number = district[1] or district[2]
                        seat = f"District {int(number) if number.isdigit() else number}"
                    elif re.search(r"\bAT ?L(?:ARGE|G)\b", plain):
                        seat = "At-Large"
                    elif position := re.search(
                        r"\bCOUNCIL (?:POSITION |POS )?(\d+)\b(?! ?(?:YEAR|YR))|\bPOS(?:ITION)? (\d+)\b", plain
                    ):
                        seat = f"Position {int(position[1] or position[2])}"
                rows = []
                for choice in choices:
                    label = re.sub(r"\s+", " ", choice.get("text") or "").strip()
                    if re.match(r"(?i)\s*no (?:nomination|candidate)", label):
                        continue
                    if re.match(
                        r"(?i)\s*(?:unresolved |unassigned |qualified )?write|scattering|personal choice", label
                    ):
                        label = "Write-ins"
                    for kind_node in choice.iter("VoteType"):
                        for place in kind_node.iter("Precinct"):
                            votes = place.get("votes") or "0"
                            rows.append(
                                {
                                    "county": county,
                                    "election": election,
                                    "contest": node.get("key"),
                                    "choice": label,
                                    "party": choice.get("party"),
                                    "place": re.sub(r"\s+", " ", (place.get("name") or "").upper()).strip(),
                                    "votes": int(votes) if votes.isdigit() else 0,
                                }
                            )
                    rows.append(
                        {
                            "county": county,
                            "election": election,
                            "contest": node.get("key"),
                            "choice": label,
                            "party": choice.get("party"),
                            "place": None,
                            "votes": int(choice.get("totalVotes") or 0),
                        }
                    )
                if not rows:
                    continue
                lines.extend(rows)
                contests.append(
                    {
                        "county": county,
                        "publisher": publisher,
                        "election": election,
                        "contest": node.get("key"),
                        "day": day,
                        "title": title,
                        "text": text,
                        "key": key,
                        "scheme": scheme,
                        "code": code,
                        "seat": seat,
                        "remainder": re.search(r"UNEX|UNXP|SHORT TERM|PARTIAL|REMAINDER|VACANCY|ENDING", plain)
                        is not None,
                        "magnitude": int(node.get("voteFor") or 1),
                        "variant": None,
                    }
                )
    groups: dict[tuple[object, ...], list[dict[str, object]]] = {}
    for row in contests:
        groups.setdefault(
            (
                *(row[k] for k in ("county", "election", "code", "key", "seat", "remainder")),
                row["text"] if row["key"] == "question" else None,
            ),
            [],
        ).append(row)
    for rows in groups.values():
        if len(rows) == 1:
            continue
        terms = [re.search(r"(\d|ONE|TWO|THREE|FOUR|FIVE|SIX) ?(?:YR|YEAR)", _plain(str(row["text"]))) for row in rows]
        named = [f"{NUMBERS.get(term[1], term[1])}-year term" if term else None for term in terms]
        ambiguous = 0
        for row, term in zip(rows, named, strict=True):
            if term and named.count(term) == 1:
                row["variant"] = term
            else:
                ambiguous += 1
                row["variant"] = f"contest {ambiguous}" if ambiguous > 1 else None
    frame = (
        pl.DataFrame(lines, schema_overrides={"votes": pl.Int64, "place": pl.String, "party": pl.String})
        .with_columns(same=pl.col("choice").str.to_lowercase().str.replace_all(r"[^a-z0-9]+", ""))
        .group_by("county", "election", "contest", "same", "place", maintain_order=True)
        .agg(pl.col("choice").first(), pl.col("party").drop_nulls().first(), pl.col("votes").sum())
        .drop("same")
    )
    contests_frame = pl.DataFrame(contests, infer_schema_length=None)
    totals = frame.filter(pl.col("place").is_null()).drop("place")
    counted = (
        frame.filter(pl.col("place").is_not_null())
        .group_by("county", "election", "contest", "choice")
        .agg(pl.col("votes").sum().alias("counted"))
    )
    checked = (
        totals.join(counted, on=["county", "election", "contest", "choice"], how="left")
        .group_by("county", "election", "contest")
        .agg((pl.col("votes") == pl.col("counted")).all().alias("balanced"))
    )
    contests_frame = contests_frame.join(checked, on=["county", "election", "contest"], how="left")
    print(
        f"{contests_frame.height} contests, {contests_frame.filter('balanced').height} with precincts that add up; "
        f"{contests_frame.filter(pl.col('key') == 'question').height} questions; "
        f"{contests_frame.select('code').n_unique()} municipalities"
    )
    PARSED.mkdir(parents=True, exist_ok=True)
    contests_frame.write_parquet(PARSED / "contests.parquet")
    frame.join(
        contests_frame.select("county", "election", "contest"), on=["county", "election", "contest"]
    ).write_parquet(PARSED / "lines.parquet")
    used = set(contests_frame.select("scheme", "code").unique().iter_rows())
    pl.DataFrame(
        [(scheme, code, *shapes[(scheme, code)]) for scheme, code in sorted(used)],
        schema=["scheme", "code", "display", "geom"],
        orient="row",
    ).write_parquet(PARSED / "polities.parquet")


def emit() -> Path:
    contests = pl.read_parquet(PARSED / "contests.parquet").sort("day", "election", "contest")
    grouped = {
        keys: rows for keys, rows in pl.read_parquet(PARSED / "lines.parquet").group_by("county", "election", "contest")
    }
    with connect() as conn:
        polities: dict[str, tuple[UUID, str, str]] = {}
        for scheme, code, polity, text, path in conn.execute(
            """select a.scheme_code, c.code, t.polity_id, n.text, s.path from area a
               join area_code c on c.area_id = a.id
               join polity_territory t on t.area_id = a.id
               join polity p on p.id = t.polity_id and p.kind in ('county', 'municipality')
               join name n on n.entity_id = p.id and n.is_preferred and n.lang = 'en'
               join slug s on s.entity_id = p.id and s.is_current
               where a.scheme_code in ('us.county', 'us.place', 'us.town') and left(c.code, 2) = any(%s)""",
            [list(STATES)],
        ).fetchall():
            polities[f"{scheme}:{code}"] = (polity, text, path)
        prefixes = [f"us/{st.lower()}/%" for st in STATES.values()]
        slugs = dict(conn.execute("select path, entity_id from slug where path like any(%s)", [prefixes]).fetchall())
        taken = set(
            conn.execute(
                """select e.date_from, x.value from contest c
                   join election e on e.id = c.election_id
                   join result_set r on r.contest_id = c.id
                   join changeset g on g.id = r.changeset_id
                   join external_id x on x.entity_id = coalesce(c.office_id, c.body_id)
                     and x.scheme in ('us.office', 'us.body')
                   where split_part(x.value, ':', 1) = any(%s) and g.pipeline <> %s""",
                [list(STATES.values()), SOURCE],
            ).fetchall()
        )
        precincts: dict[str, UUID] = dict(
            conn.execute(
                """select code, id from reporting_unit where scheme_code = 'us.precinct'
                   and split_part(code, ':', 3) = any(%s)""",
                [list(COUNTIES)],
            ).fetchall()
        )
        foreign = {":".join(code.split(":")[:3]) for code in precincts}
        counties: dict[str, tuple[UUID, UUID]] = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and code = any(%s)",
                [list(COUNTIES)],
            ).fetchall()
        }
        parents: dict[str, tuple[str, str]] = {}
        for value, kind in conn.execute(
            """select x.value, e.kind from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}:(IL|KS|MI|NE|OH|PA)(:[a-z_]+)?$'
                 and e.kind <> 'primary'
               order by length(x.value)"""
        ).fetchall():
            parents.setdefault(":".join(value.split(":")[:2]), (value, kind))
        held = set(parents)
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="City, village, borough, and township elections and local questions from county election night reporting sites in Illinois, Kansas, Michigan, Nebraska, Ohio, and Pennsylvania",
            polity="US",
        )
        zeroed = cs.zeroed() | cs.remainders(
            "A scattered-votes line in Clarity's results read as a candidate", r"^scattered$"
        )

        cs.renamed.update(RENAMED)

        def name(entity: UUID, text: str) -> None:
            cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        def title(text: str) -> str:
            if text != text.upper():
                return text
            text = re.sub(
                r"\b([A-Z][A-Z'’-]+)\b", lambda match: match[1].title() if len(match[1]) > 1 else match[1], text
            )
            return re.sub(r"\bMc([a-z])", lambda match: "Mc" + match[1].upper(), text)

        systems = {key: cs.key("us.electoral_system", key) for key in ("fptp", "block", "referendum")}
        if cs.minted & set(systems.values()):
            raise ValueError("the fptp, block, and referendum systems must already exist")
        census = cs.source("U.S. Census Bureau", "Cartographic Boundary Files, 1:500,000")
        for link in (*SUBDIVISIONS.values(), *PLACES.values()):
            cs.artifact(raw.cached(SOURCE, link), census)
        sources = {}
        for county, (clarity, publisher, elections) in COUNTIES.items():
            sources[county] = cs.source(publisher, TITLE)
            cs.add(
                "source",
                id=sources[county],
                kind="official_body",
                publisher=publisher,
                title=TITLE,
                url=f"https://results.enr.clarityelections.com/{STATES[county[:2]]}/{clarity}/",
                license="Public record",
            )
            for election, version in elections.items():
                cs.artifact(raw.cached(SOURCE, url(county, election, version)), sources[county])
        made = pl.read_parquet(PARSED / "polities.parquet")
        repeated = set(made.filter(pl.col("display").is_duplicated())["display"].to_list())
        for scheme, code, display, geom in made.iter_rows():
            if f"{scheme}:{code}" in polities:
                continue
            st = STATES[code[:2]]
            polity = cs.key("us.polity", f"{'town' if scheme == 'us.town' else 'place'}:{code}")
            path = f"us/{st.lower()}/{slug(display)}"
            if (display in repeated or path in slugs and slugs[path] != polity) and scheme == "us.town":
                path = f"{path}-{slug(polities[f'us.county:{code[:5]}'][1])}"
            if path in slugs and slugs[path] != polity:
                path = f"{path}-{'township' if scheme == 'us.town' else 'village'}"
            if path in slugs and slugs[path] != polity:
                raise ValueError(f"{display} would take {path}")
            slugs[path] = polity
            polities[f"{scheme}:{code}"] = (polity, display, path)
            if polity not in cs.minted:
                continue
            cs.add("polity", id=polity, kind="municipality", iso_code=None)
            name(polity, display)
            cs.add("polity_parent", polity_id=polity, parent_id=cs.polity(f"US-{st}"))
            cs.add("slug", path=path, entity_id=polity)
            area = cs.area(scheme, code)
            cs.add("area", id=area, scheme_code=scheme)
            cs.add("area_code", area_id=area, code=code, valid_from=None)
            cs.add(
                "area_parent",
                area_id=area,
                parent_id=cs.area("us.county", code[:5]) if scheme == "us.town" else cs.area("us.state", code[:2]),
            )
            name(area, f"{display}, {st}")
            cs.add("polity_territory", polity_id=polity, area_id=area)
            cs.add(
                "area_geometry",
                area_id=area,
                valid_from=None,
                geom=geom,
                label_point=None,
                quality="official",
                source_id=census,
            )
            cs.add(
                "reporting_unit",
                id=cs.unit(scheme, code),
                polity_id=polity,
                scheme_code=scheme,
                code=code,
                area_relation="equals",
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        owners: dict[str, UUID] = {}
        elections: set[UUID] = set()
        persons: dict[tuple[str, str], UUID] = {}
        districts: set[str] = set()
        minted: dict[str, tuple[str, date]] = {}
        memberships: set[tuple[UUID, UUID, date]] = set()
        values: list[tuple[str, str, str, int]] = []
        emitted: list[UUID] = []
        for row in contests.iter_rows(named=True):
            scheme, code, key, seat, day = row["scheme"], row["code"], row["key"], row["seat"], row["day"]
            st = STATES[code[:2]]
            polity, place, path = polities[f"{scheme}:{code}"]
            question = key == "question"
            if not question and (day, f"{st}:{code}:{key}") in taken:
                continue
            special = "SPECIAL" in str(row["title"]).upper()
            if f"{day}:{st}" not in parents:
                parents[f"{day}:{st}"] = (
                    f"{day}:{st}",
                    "special" if special else "general" if day.month == 11 and day.day <= 8 else "local",
                )
            parent_key, kind = parents[f"{day}:{st}"]
            parent = cs.key("us.election", parent_key)
            if f"{day}:{st}" not in held and parent not in elections:
                elections.add(parent)
                cs.add(
                    "election",
                    id=parent,
                    polity_id=cs.polity(f"US-{st}"),
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(
                    parent,
                    f"{day.year} {STATE_NAMES[st]} "
                    + ("general election" if kind == "general" else f"{kind} elections"),
                )
                cs.add("slug", path=f"us/{st.lower()}/{day}-{kind}", entity_id=parent)
            election = cs.key("us.election", f"{day}:{st}:{code}")
            if election not in elections:
                elections.add(election)
                cs.add(
                    "election",
                    id=election,
                    polity_id=polity,
                    parent_id=parent,
                    kind=kind,
                    date_from=day,
                    date_to=day,
                    date_precision="day",
                    status="held",
                )
                name(election, f"{day.year} {place} {kind} election")
                cs.add("slug", path=f"{path}/{day}-{kind}", entity_id=election)
            whole = cs.area(scheme, code)
            area, unit = whole, cs.unit(scheme, code)
            label = re.sub(r"\s*\((?:VOTE|Vote) [^)]*\)|\s+-?\s*Vote for \w+$", "", str(row["text"])).strip()
            contest_key = (
                f"{day}:{st}:{code}:{key}"
                + (f":{slug(label)}" if question else "")
                + (f":{slug(seat)}" if seat else "")
                + (":remainder" if row["remainder"] else "")
                + (f":{slug(row['variant'])}" if row["variant"] else "")
            )
            contest_path = (
                f"{path}/{day}-{kind}/"
                + (slug(label)[:60].rstrip("-") if question else key)
                + (f"-{slug(seat)}" if seat else "")
                + ("-unexpired" if row["remainder"] else "")
                + (f"-{slug(row['variant'])}" if row["variant"] else "")
            )
            contest = cs.key("us.contest", contest_key)
            if contest_path in slugs and slugs[contest_path] != contest:
                continue
            slugs[contest_path] = contest
            if question:
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="ballot_question",
                    district_area_id=area,
                    electoral_system_id=systems["referendum"],
                    round_number=1,
                    default_result_kind="counted",
                )
                cs.add("ballot_question", contest_id=contest, type="mandatory_referendum")
                name(contest, f"{day.year} {place}: {title(label)}")
            else:
                body = key in BODIES
                office_title = {
                    "mayor": f"Mayor of {place}",
                    "president": f"{place} President",
                    "council": f"{place} Council",
                    "council-president": f"{place} Council President",
                    "board-of-trustees": f"{place} Board of Trustees",
                    "township-board": f"{place} Board of Trustees",
                    "board-of-supervisors": f"{place} Board of Supervisors",
                    "board-of-commissioners": f"{place} Board of Commissioners",
                    "clerk-treasurer": f"{place} Clerk-Treasurer",
                    "auditors": f"{place} Auditors",
                    "auditor": f"{place} Auditor",
                    "supervisor": f"{place} Supervisor",
                    "trustee": f"{place} Trustee",
                    "highway-commissioner": f"{place} Highway Commissioner",
                    "assessor": f"{place} Assessor",
                    "collector": f"{place} Tax Collector" if st == "PA" else f"{place} Collector",
                    "fiscal-officer": f"{place} Fiscal Officer",
                    "law-director": f"{place} Law Director",
                    "clerk": f"{place} Clerk",
                    "treasurer": f"{place} Treasurer",
                }[key]
                if f"{code}:{key}" not in owners:
                    owner = owners[f"{code}:{key}"] = cs.key("us.body" if body else "us.office", f"{st}:{code}:{key}")
                    if owner in cs.minted:
                        if body:
                            cs.add("body", id=owner, polity_id=polity, kind="council")
                        else:
                            cs.add("office", id=owner, polity_id=polity, kind="mayor" if key == "mayor" else "other")
                        name(owner, office_title)
                if seat and seat.startswith(("Ward", "District")):
                    district = f"{code[:2]}:{code}:{key}:{slug(seat)}"
                    area, unit = cs.area("us.local_district", district), cs.unit("us.local_district", district)
                    if area in cs.minted and district not in districts:
                        districts.add(district)
                        cs.add("area", id=area, scheme_code="us.local_district")
                        cs.add("area_code", area_id=area, code=district, valid_from=None)
                        cs.add("area_parent", area_id=area, parent_id=whole, valid_from=None)
                        name(area, f"{office_title}, {seat}")
                        cs.add(
                            "reporting_unit",
                            id=unit,
                            polity_id=polity,
                            scheme_code="us.local_district",
                            code=district,
                            area_relation="equals",
                            area_id=area,
                            kind="standard",
                        )
                magnitude = row["magnitude"]
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats" if body else "office",
                    office_id=None if body else owners[f"{code}:{key}"],
                    body_id=owners[f"{code}:{key}"] if body else None,
                    district_area_id=area,
                    magnitude=magnitude,
                    round_number=1,
                    electoral_system_id=systems["fptp" if magnitude == 1 else "block"],
                    default_result_kind="counted",
                    term="remainder" if row["remainder"] else "full",
                )
                name(
                    contest,
                    (
                        f"{day.year} {place} mayoral election"
                        if key == "mayor"
                        else f"{day.year} {office_title} election"
                    )
                    + (f", {seat}" if seat else "")
                    + (" (unexpired term)" if row["remainder"] else "")
                    + (f" ({row['variant']})" if row["variant"] else ""),
                )
            cs.add("slug", path=contest_path, entity_id=contest)
            emitted.append(contest)
            counted = grouped[(row["county"], row["election"], row["contest"])]
            jurisdiction = COUNTIES[row["county"]][0].replace("_", " ").upper()
            prefix = f"{day.year}:{st}:{row['county']}"
            places = counted.filter(pl.col("place").is_not_null())
            precise = bool(row["balanced"]) and (
                prefix not in foreign
                or all(f"{prefix}:{jurisdiction}:{name}" in precincts for name in places["place"].unique())
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
                source_id=sources[row["county"]],
                is_default=True,
                completeness=1,
            )
            cs.add(
                "note",
                entity_id=result_set,
                kind="methodology",
                body_md=(METHOD if precise else TOTALS).format(publisher=row["publisher"]),
            )
            totals = counted.filter(pl.col("place").is_null()).sort("votes", "choice", descending=[True, False])
            options: dict[str, UUID] = {}
            ranked: list[tuple[int, UUID]] = []
            for choice, party, votes in totals.select("choice", "party", "votes").iter_rows():
                if question:
                    answer = "yes" if re.match(r"(?i)\s*(?:yes|for)\b", choice) else "no"
                    option = options[choice] = cs.key("us.option", f"{contest_key}:{answer}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="answer",
                        ballot_position=1 if answer == "yes" else 2,
                        ballot_label=answer.capitalize(),
                    )
                elif re.match(r"(?i)write|scatter(?:ing|ed)|personal choice", choice):
                    option = cs.key("us.option", f"{contest_key}:write-ins")
                    if option not in options.values():
                        cs.add(
                            "contest_option", id=option, contest_id=contest, kind="write_in", ballot_label="Write-ins"
                        )
                    options[choice] = option
                    continue
                else:
                    ballot = _title(re.sub(r"\s*\((?:DEM|REP|IND|NON|NP|NPA|LIB|GRN)\)$", "", choice))
                    person_key = (code, slug(ballot))
                    if person_key not in persons:
                        persons[person_key] = cs.key(f"{SOURCE}.person", f"{st}:{code}:{slug(ballot)}")
                        if persons[person_key] in cs.minted:
                            cs.add("person", id=persons[person_key])
                            name(persons[person_key], ballot)
                    option = options[choice] = cs.key("us.option", f"{contest_key}:{slug(ballot)}")
                    party_key = PARTIES.get((party or "").upper()[:3])
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=cs.key("us.party", party_key) if party_key else None,
                        ballot_label=ballot,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{contest_key}:{slug(ballot)}:head"),
                        person_id=persons[person_key],
                        option_id=option,
                        role="head",
                        ballot_name=ballot,
                        is_independent=False,
                    )
                ranked.append((votes, option))
            if result_set not in cs.minted and result_set not in zeroed:
                continue
            if question:
                if len(ranked) == 2 and ranked[0][0] != ranked[1][0]:
                    cs.add("outcome", result_set_id=result_set, option_id=ranked[0][1], kind="won")
            else:
                cut = row["magnitude"]
                if ranked and (len(ranked) <= cut or ranked[cut - 1][0] != ranked[cut][0]):
                    for _, option in ranked[:cut]:
                        cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
            if not precise:
                for choice, votes in totals.select("choice", "votes").iter_rows():
                    values.append((str(result_set), str(unit), str(options[choice]), votes))
                continue
            for choice, place_name, votes in (
                places.group_by("choice", "place")
                .agg(pl.col("votes").sum())
                .select("choice", "place", "votes")
                .iter_rows()
            ):
                place_code = f"{prefix}:{jurisdiction}:{place_name}"
                target = precincts.get(place_code) or cs.unit("us.precinct", place_code)
                if place_code not in precincts:
                    minted.setdefault(place_code, (row["county"], day))
                memberships.add((target, area, day))
                values.append((str(result_set), str(target), str(options[choice]), votes))
        columns = ("id", "election_id", "contest_id", "stage", "kind", "source_id", "is_default", "completeness")
        for row in conn.execute(
            f"""select {", ".join(f"r.{column}" for column in columns)} from result_set r
                join changeset g on g.id = r.changeset_id
                where g.pipeline = %s and r.status = 'active' and r.contest_id <> all(%s)""",
            [SOURCE, emitted],
        ).fetchall():
            cs.add("result_set", **dict(zip(columns, row, strict=True)), status="withdrawn")
            cs.add("note", entity_id=row[0], kind="caveat", body_md=WITHDRAWN)
        for place_code, (county, day) in minted.items():
            cs.add(
                "reporting_unit",
                id=cs.unit("us.precinct", place_code),
                polity_id=cs.polity(f"US-{STATES[county[:2]]}"),
                scheme_code="us.precinct",
                code=place_code,
                parent_id=counties[county][0],
                area_relation="within",
                area_id=counties[county][1],
                kind="standard",
                valid_from=day,
                valid_to=day + timedelta(days=1),
            )
        for target, area, day in memberships:
            cs.add(
                "reporting_unit_area", unit_id=target, area_id=area, valid_from=day, valid_to=day + timedelta(days=1)
            )
        cs.frame(
            "result_value",
            pl.DataFrame(
                values,
                schema={"result_set_id": pl.String, "unit_id": pl.String, "option_id": pl.String, "value": pl.Int64},
                orient="row",
            )
            .group_by("result_set_id", "unit_id", "option_id", maintain_order=True)
            .agg(pl.col("value").sum())
            .with_columns(
                channel=pl.lit(0, pl.Int16),
                measure=pl.lit(1, pl.Int16),
                count_round=pl.lit(0, pl.Int16),
                flags=pl.lit(0, pl.Int16),
            ),
        )
        return cs.write()
