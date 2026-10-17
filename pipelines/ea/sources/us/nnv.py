import difflib
import json
import re
import tarfile
import unicodedata
import xml.etree.ElementTree as ET
from collections import Counter
from datetime import date, timedelta
from pathlib import Path
from uuid import UUID

import polars as pl
from psycopg.types.range import Range

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.clerk import LEGISLATORS, plain
from ea.sources.us.party_labels import NATIONAL, UNAFFILIATED
from ea.store import DATA, connect

SOURCE = "us.nnv"
VERSION = "1"
COMMIT = "13e2f82692c76e3695f6cd609a5e710d6cab5e26"
ARCHIVE = f"https://codeload.github.com/mapping-elections/nnv-xml/tar.gz/{COMMIT}"
PUBLISHER = "American Antiquarian Society and Tufts University"
TITLE = "A New Nation Votes: American Election Returns, 1787-1825"
LANDING = "https://elections.lib.tufts.edu/"
LICENSE = "CC0 1.0"
LICENSE_URL = "https://creativecommons.org/publicdomain/zero/1.0/"
PARSED = DATA / "parsed" / SOURCE
NS = "{http://dca.tufts.edu/aas}"
ORDINALS = {
    word: number
    for number, word in enumerate(
        [
            "first",
            "second",
            "third",
            "fourth",
            "fifth",
            "sixth",
            "seventh",
            "eighth",
            "ninth",
            "tenth",
            "eleventh",
            "twelfth",
            "thirteenth",
            "fourteenth",
            "fifteenth",
            "sixteenth",
            "seventeenth",
            "eighteenth",
            "nineteenth",
            "twentieth",
        ],
        start=1,
    )
} | {"eight": 8, "thirtieth": 30}
TENS = {"twenty": 20, "thirty": 30}
TITLES = {
    "jr", "sr", "esq", "col", "gen", "dr", "capt", "maj", "hon", "rev", "judge", "mr", "general", "colonel", "major",
    "captain", "doctor", "ii", "iii",
}  # fmt: skip
TRIALS = ["first", "second", "third", "fourth", "fifth", "sixth", "seventh", "eighth", "ninth", "tenth"]
MAJORITY = {"MA", "NH", "VT", "RI", "ME"}
POOLED = {"scattering", "unknown", "scattering votes", "scatterings", "others", "all others"}
BLANK = re.compile(r"(?i)^blanks?\b")
KINDS = {"Populated Place": "place"}
SUCCESSORS = {
    "MA": ("ME",),
    "VA": ("WV", "KY"),
    "NC": ("TN",),
    "GA": ("AL", "MS"),
    "MS": ("AL",),
    "MO": ("AR",),
    "MI": ("WI",),
    "IN": ("IL",),
}
ALIASES = {
    ("AL", "clark"): "clarke", ("AL", "stclaire"): "stclair", ("GA", "clark"): "clarke", ("GA", "dooley"): "dooly",
    ("GA", "oglethrope"): "oglethorpe", ("GA", "scriven"): "screven", ("IN", "clarke"): "clark",
    ("KY", "brackin"): "bracken", ("KY", "breckenridge"): "breckinridge", ("KY", "bullett"): "bullitt",
    ("KY", "burbon"): "bourbon", ("KY", "clarke"): "clark", ("KY", "daveiss"): "daviess", ("KY", "greene"): "green",
    ("KY", "harden"): "hardin", ("KY", "muhlenburg"): "muhlenberg", ("KY", "pendelton"): "pendleton",
    ("KY", "whitely"): "whitley", ("LA", "eastbatonrogue"): "eastbatonrouge", ("LA", "lafrouche"): "lafourche",
    ("LA", "plaquemine"): "plaquemines", ("LA", "plaquimine"): "plaquemines", ("LA", "pointcoupee"): "pointecoupee",
    ("LA", "rapide"): "rapides", ("LA", "westbatonrogue"): "westbatonrouge", ("LA", "stjohn"): "stjohnthebaptist",
    ("MA", "kennebeck"): "kennebec", ("MA", "hanckcok"): "hancock", ("MD", "queenanns"): "queenannes",
    ("MD", "mongomery"): "montgomery", ("MD", "worchester"): "worcester", ("MO", "calloway"): "callaway",
    ("MO", "stgenevieve"): "stegenevieve", ("MS", "clairborne"): "claiborne", ("NC", "cateret"): "carteret",
    ("NC", "green"): "greene", ("NC", "tyrell"): "tyrrell", ("NH", "chesire"): "cheshire",
    ("NH", "stafford"): "strafford", ("NJ", "bergin"): "bergen", ("NJ", "gloucrester"): "gloucester",
    ("NJ", "mommouth"): "monmouth", ("NY", "chautauque"): "chautauqua", ("NY", "rennselaer"): "rensselaer",
    ("PA", "alleghany"): "allegheny", ("PA", "huntington"): "huntingdon", ("SC", "marlborough"): "marlboro",
    ("SC", "orangeburgh"): "orangeburg", ("TN", "claibourne"): "claiborne", ("TN", "clairborne"): "claiborne",
    ("TN", "green"): "greene", ("VA", "accomac"): "accomack", ("VA", "albermarle"): "albemarle",
    ("VA", "berkley"): "berkeley", ("VA", "culpepper"): "culpeper", ("VA", "greenville"): "greensville",
    ("VA", "kenawha"): "kanawha", ("VA", "kenhawa"): "kanawha", ("VA", "lunenberg"): "lunenburg",
    ("VA", "matthews"): "mathews", ("VA", "mecklenberg"): "mecklenburg", ("VA", "monogalia"): "monongalia",
    ("VA", "nottaway"): "nottoway", ("VA", "pittslyvania"): "pittsylvania", ("VA", "surrey"): "surry",
    ("VA", "nanesmond"): "nansemond", ("VA", "norfolk"): "norfolkcounty", ("VA", "norfolkborough"): "norfolkcity",
    ("VT", "grandeisle"): "grandisle", ("VT", "jefferson"): "washington",
}  # fmt: skip
MONTH_NOTE = "A New Nation Votes gives only the month of this election, so the date shown is the first of that month."
YEAR_NOTE = "A New Nation Votes gives only the year of this election, so the date shown is January 1 of that year."


def fetch() -> None:
    raw.fetch(SOURCE, ARCHIVE, suffix=".tar.gz")


def _ballot(text: str) -> int | None:
    words = re.sub(r"[^a-z ]", " ", text.lower()).replace(" ballot", "").split()
    if len(words) == 2 and words[0] in TENS and words[1] in ORDINALS:
        return TENS[words[0]] + ORDINALS[words[1]]
    return ORDINALS.get(words[0]) if len(words) == 1 else None


def _number(text: str | None) -> float | None:
    if text is None or text.strip() in ("", "null"):
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def _null(text: str | None) -> str | None:
    return None if text is None or text.strip() in ("", "null") else text.strip()


def parse() -> None:
    records: list[dict[str, object]] = []
    candidates: list[dict[str, object]] = []
    units: list[dict[str, object]] = []
    results: list[dict[str, object]] = []
    with tarfile.open(raw.cached(SOURCE, ARCHIVE).path) as tar:
        for member in tar.getmembers():
            path = member.name.partition("/")[2]
            if not member.isfile() or not path.endswith(".xml"):
                continue
            handle = tar.extractfile(member)
            if handle is None:
                continue
            record = ET.parse(handle).getroot()
            office = record.find(f"{NS}office")
            role = office.find(f"{NS}role") if office is not None else None
            if office is None or role is None:
                continue
            election = record.get("election_id", "")
            references = [
                (reference.get("type"), reference.get("urn") or (reference.text or "").strip())
                for reference in record.iter(f"{NS}reference")
            ]
            records.append(
                {
                    "record": election,
                    "file": path,
                    "folder": path.split("/")[0],
                    "handle": record.get("handle"),
                    "label": record.get("label"),
                    "type": record.get("type"),
                    "iteration": record.get("iteration"),
                    "ballot": _ballot(record.get("iteration", "")),
                    "date": _null(record.get("date")),
                    "office": office.get("name"),
                    "office_id": office.get("office_id"),
                    "office_scope": office.get("scope"),
                    "role": role.get("title"),
                    "role_scope": role.get("scope"),
                    "citations": [text for kind, text in references if kind == "citation" and text],
                    "images": [text for kind, text in references if kind == "page_image" and text],
                    "notes": [
                        (note.text or "").strip()
                        for parent in (record, office, role)
                        for note in parent.findall(f"{NS}note")
                        if (note.text or "").strip()
                    ],
                }
            )
            totals = {
                summary.get("candidate_ref"): _number(summary.get("vote_total"))
                for summary in role.iter(f"{NS}candidate_summary")
            }
            for candidate in role.iter(f"{NS}candidate"):
                notes = [(note.text or "").strip() for note in candidate.findall(f"{NS}note")]
                candidates.append(
                    {
                        "record": election,
                        "candidate": candidate.get("candidate_num"),
                        "name": (candidate.get("name") or "").strip(),
                        "name_id": _null(candidate.get("name_id")),
                        "affiliation": _null(candidate.get("affiliation")),
                        "affiliation_id": _null(candidate.get("affiliation_id")),
                        "total": totals.get(candidate.get("candidate_num")),
                        "elected": any(re.match(r"(?i)elected\b", note) for note in notes),
                        "notes": [note for note in notes if note],
                    }
                )
            counter = 0

            def walk(element: ET.Element, parent: int | None, depth: int, election: str = election) -> None:
                nonlocal counter
                node = counter
                counter += 1
                units.append(
                    {
                        "record": election,
                        "node": node,
                        "parent": parent,
                        "depth": depth,
                        "type": _null(element.get("type")),
                        "name": _null(element.get("name")),
                        "notes": [
                            (note.text or "").strip()
                            for note in element.findall(f"{NS}note")
                            if (note.text or "").strip()
                        ],
                    }
                )
                for result in element.findall(f"{NS}result"):
                    results.append(
                        {
                            "record": election,
                            "node": node,
                            "candidate": result.get("candidate_ref"),
                            "text": result.get("vote"),
                            "vote": _number(result.get("vote")),
                        }
                    )
                for child in element.findall(f"{NS}sub_unit"):
                    walk(child, node, depth + 1)

            for admin in role.findall(f"{NS}admin_unit"):
                walk(admin, None, 0)
    own: dict[tuple[str, int], dict[str, float]] = {}
    for result in results:
        if result["vote"] is not None:
            cell = own.setdefault((str(result["record"]), int(str(result["node"]))), {})
            cell[str(result["candidate"])] = cell.get(str(result["candidate"]), 0) + float(str(result["vote"]))
    children: dict[tuple[str, int], list[int]] = {}
    roots: dict[str, list[int]] = {}
    for unit in units:
        if unit["parent"] is None:
            roots.setdefault(str(unit["record"]), []).append(int(str(unit["node"])))
        else:
            children.setdefault((str(unit["record"]), int(str(unit["parent"]))), []).append(int(str(unit["node"])))
    effective: dict[tuple[str, int], dict[str, float]] = {}

    def total(key: tuple[str, int]) -> dict[str, float]:
        if key not in effective:
            summed: dict[str, float] = {}
            for child in children.get(key, []):
                for candidate, vote in total((key[0], child)).items():
                    summed[candidate] = summed.get(candidate, 0) + vote
            effective[key] = own.get(key) or summed
        return effective[key]

    def finest(key: tuple[str, int]) -> list[int]:
        kids = [child for child in children.get(key, []) if total((key[0], child))]
        mine = own.get(key)
        if not kids:
            return [key[1]] if mine else []
        if mine:
            summed: dict[str, float] = {}
            for child in kids:
                for candidate, vote in total((key[0], child)).items():
                    summed[candidate] = summed.get(candidate, 0) + vote
            if summed != mine:
                return [key[1]]
        return [node for child in kids for node in finest((key[0], child))]

    leaves = [
        {"record": record, "node": node}
        for record, nodes in roots.items()
        for root in nodes
        for node in finest((record, root))
    ]
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(leaves, schema={"record": pl.String, "node": pl.Int64}).write_parquet(PARSED / "leaves.parquet")
    pl.DataFrame(records, infer_schema_length=None).write_parquet(PARSED / "records.parquet")
    pl.DataFrame(candidates, infer_schema_length=None).write_parquet(PARSED / "candidates.parquet")
    pl.DataFrame(units, infer_schema_length=None).write_parquet(PARSED / "units.parquet")
    pl.DataFrame(results, infer_schema_length=None).write_parquet(PARSED / "results.parquet")


def congress(day: date) -> int:
    return (day.year - 1789 - ((day.month, day.day) < (3, 4))) // 2 + 1


def _words(text: str) -> list[str]:
    plain = re.sub(
        r"[^a-z ]",
        " ",
        unicodedata.normalize("NFKD", re.sub(r"\(.*?\)", "", text)).encode("ascii", "ignore").decode().lower(),
    )
    return [word for word in plain.split() if word not in TITLES]


def _alike(one: list[str], other: list[str]) -> float:
    if not one or not other:
        return 0
    ratio = difflib.SequenceMatcher(None, one[-1], other[-1]).ratio()
    if ratio < 0.8 or (len(one) > 1 and len(other) > 1 and one[0][0] != other[0][0]):
        return 0
    return ratio


def _county(text: str) -> str:
    return plain(re.sub(r"(?i)\b(county|parish|district)\b", "", text.replace("Saint ", "St ").replace("&", "and")))


def emit() -> Path:
    records = pl.read_parquet(PARSED / "records.parquet").with_columns(
        postal=pl.col("record").str.slice(0, 2).str.to_uppercase(),
        year=pl.col("date").str.slice(0, 4).cast(pl.Int32, strict=False),
        month=pl.col("date").str.slice(5, 2).cast(pl.Int32, strict=False),
    )
    house = records.filter(
        (pl.col("office") == "U.S. House of Representatives")
        & pl.col("type").is_in(["General", "Special"])
        & pl.col("role_scope").is_in(["District", "State"])
        & pl.col("year").is_between(1787, 1823)
        & (pl.col("folder") != "USA")
    )
    wanted = list(house["record"])
    nodes: dict[tuple[str, int], tuple[int | None, str | None, str | None, int]] = {
        (record, node): (parent, kind, name, depth)
        for record, node, parent, kind, name, depth in pl.read_parquet(PARSED / "units.parquet")
        .filter(pl.col("record").is_in(wanted))
        .select("record", "node", "parent", "type", "name", "depth")
        .iter_rows()
    }
    cells: dict[tuple[str, int], dict[str, float]] = {}
    for record, node, candidate, vote in (
        pl.read_parquet(PARSED / "results.parquet")
        .filter(pl.col("record").is_in(wanted) & pl.col("vote").is_not_null())
        .select("record", "node", "candidate", "vote")
        .iter_rows()
    ):
        cell = cells.setdefault((record, node), {})
        cell[candidate] = cell.get(candidate, 0) + vote
    leaves: dict[str, list[int]] = {}
    for record, node in pl.read_parquet(PARSED / "leaves.parquet").filter(pl.col("record").is_in(wanted)).iter_rows():
        leaves.setdefault(record, []).append(node)
    lines: dict[str, list[dict]] = {}
    for line in (
        pl.read_parquet(PARSED / "candidates.parquet").filter(pl.col("record").is_in(wanted)).iter_rows(named=True)
    ):
        lines.setdefault(line["record"], []).append(line)
    roster: dict[tuple[int, str], list[dict]] = {}
    for kind in ("historical", "current"):
        for person in json.loads(raw.cached("us.clerk", LEGISLATORS.format(kind=kind)).path.read_text()):
            for term in person["terms"]:
                began = date.fromisoformat(term["start"])
                if term["type"] != "rep" or began.year > 1830:
                    continue
                full = " ".join(
                    part
                    for part in (person["name"]["first"], person["name"].get("middle"), person["name"]["last"])
                    if part
                )
                for number in range(congress(began), congress(date.fromisoformat(term["end"]) - timedelta(days=1)) + 1):
                    roster.setdefault((number, term["state"]), []).append(
                        {
                            "words": _words(full),
                            "bioguide": person["id"]["bioguide"],
                            "district": max(int(term.get("district", 0)), 0),
                            "start": began,
                            "first": congress(began) == number,
                        }
                    )
    sessions: dict[int, Counter[date]] = {}
    for (number, _), members in roster.items():
        for member in members:
            if member["first"]:
                sessions.setdefault(number, Counter())[member["start"]] += 1
    opened = {number: counter.most_common(1)[0][0] for number, counter in sessions.items()}

    def path(record: str, node: int) -> tuple[tuple[str, str], ...]:
        chain = []
        key: int | None = node
        while key is not None:
            parent, kind, name, depth = nodes[(record, key)]
            chain.append((kind, name, depth))
            key = parent
        return tuple(
            (kind or "unit", name)
            for kind, name, depth in reversed(chain[:-1])
            if name is not None and not (depth == 1 and kind in (None, "District"))
        )

    returns: dict[str, dict[tuple[tuple[str, str], ...], dict[str, float]]] = {}
    for record in wanted:
        for node in leaves.get(record, []):
            where = path(record, node)
            cell = returns.setdefault(record, {}).setdefault(where, {})
            for candidate, vote in cells[(record, node)].items():
                cell[candidate] = cell.get(candidate, 0) + vote

    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="House returns by county and town, 1788–1823, from A New Nation Votes: attached to THEA's contests "
            "where they reconcile, and the contests THEA lacks",
            polity="US",
        )
        us = cs.polity("US")
        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="academic",
            publisher=PUBLISHER,
            title=TITLE,
            url=LANDING,
            license=LICENSE,
            license_url=LICENSE_URL,
        )
        cs.artifact(raw.cached(SOURCE, ARCHIVE), source)
        cs.add("scheme", code="us.return_unit", polity_id=us, kind="synthetic", rank=3, noun="return unit")

        def named(entity: UUID, text: str) -> None:
            if entity in cs.minted:
                cs.add("name", entity_id=entity, text=text, lang="en", kind="official", is_preferred=True)

        states: dict[str, tuple[UUID, UUID, str, str]] = {
            iso.removeprefix("US-"): (unit, area, text, code)
            for text, iso, unit, area, code in conn.execute(
                """select n.text, p.iso_code, u.id, u.area_id, u.code from reporting_unit u
                   join polity p on p.id = u.polity_id
                   join name n on n.entity_id = p.id and n.is_preferred
                   where u.scheme_code = 'us.state'"""
            ).fetchall()
        }
        counties: dict[tuple[str, str], tuple[UUID, UUID]] = {}
        for code, text, unit, area in conn.execute(
            """select u.code, n.text, u.id, u.area_id from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county'"""
        ).fetchall():
            postal = next(state for state, entry in states.items() if entry[3] == code[:2])
            counties[(postal, plain(text) + ("city" if int(code[2:]) >= 500 else ""))] = (unit, area)
        for code, unit, area in conn.execute(
            "select code, id, area_id from reporting_unit where scheme_code = 'us.former_county'"
        ).fetchall():
            postal, _, key = code.partition(":")
            counties.setdefault((postal, key), (unit, area))
        made: dict[str, tuple[UUID, UUID]] = {}

        def unit(postal: str, where: tuple[tuple[str, str], ...]) -> UUID:
            kind, name = where[0]
            if kind in ("County", "Parish") or (kind == "District" and postal == "SC"):
                target = ALIASES.get((postal, _county(name)), _county(name))
                prefix = f"{postal}:{target}"
                if prefix not in made:
                    if (postal, target) in counties:
                        made[prefix] = counties[(postal, target)]
                    else:
                        moved = next(
                            (
                                counties[(other, target)]
                                for other in SUCCESSORS.get(postal, ())
                                if (other, target) in counties
                            ),
                            None,
                        )
                        area = moved[1] if moved else states[postal][1]
                        former = cs.unit("us.former_county", prefix)
                        if former in cs.minted:
                            cs.add(
                                "reporting_unit",
                                id=former,
                                polity_id=cs.polity(f"US-{postal}"),
                                scheme_code="us.former_county",
                                code=prefix,
                                parent_id=states[postal][0],
                                area_relation="within",
                                area_id=area,
                                kind="standard",
                                valid_from=None,
                                valid_to=None,
                            )
                            named(former, name)
                        made[prefix] = (former, area)
                rest = where[1:]
            elif kind == "City" and (postal, plain(name) + "city") in counties:
                prefix = f"{postal}:{plain(name)}city"
                made.setdefault(prefix, counties[(postal, plain(name) + "city")])
                rest = where[1:]
            else:
                prefix = f"{postal}:"
                made.setdefault(prefix, states[postal][:2])
                rest = where
            parent, area = made[prefix]
            for kind, name in rest:
                prefix = f"{prefix}:{KINDS.get(kind, slug(kind))}:{plain(name)}"
                if prefix not in made:
                    child = cs.unit("us.return_unit", prefix)
                    if child in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=child,
                            polity_id=cs.polity(f"US-{postal}"),
                            scheme_code="us.return_unit",
                            code=prefix,
                            parent_id=parent,
                            area_relation="within",
                            area_id=area,
                            kind="standard",
                            valid_from=None,
                            valid_to=None,
                        )
                        label = (
                            name
                            if kind in ("Town", "Populated Place", "unit") or name.lower().endswith(kind.lower())
                            else f"{name} {kind}"
                        )
                        named(child, label)
                    made[prefix] = (child, area)
                parent = made[prefix][0]
            return parent

        held: dict[str, dict] = {}
        for (
            key,
            contest,
            election,
            magnitude,
            result_set,
            kind,
            option,
            option_kind,
            label,
            nested,
            votes,
            won,
            person,
        ) in conn.execute(
            """select x.value, c.id, c.election_id, c.magnitude, r.id, r.kind, o.id, o.kind, o.ballot_label,
                          exists (select from contest_option ch where ch.parent_option_id = o.id),
                          (select sum(v.value)::bigint from result_value v
                           where v.result_set_k = r.k and v.option_k = o.k and v.measure = 1),
                          exists (select from outcome oc where oc.result_set_id = r.id and oc.option_id = o.id
                                  and oc.kind = 'elected'),
                          (select cd.person_id from candidacy cd where cd.option_id = o.id and cd.role = 'head' limit 1)
                   from external_id x join contest c on c.id = x.entity_id
                   join result_set r on r.contest_id = c.id and r.is_default
                   left join contest_option o on o.contest_id = c.id and o.parent_option_id is null
                   where x.scheme = 'us.contest' and x.value like '%%:house:%%' and x.value ~ '^1[78][0-9]{2}'
                     and left(x.value, 4)::int < 1824"""
        ).fetchall():
            entry = held.setdefault(
                key,
                {"contest": contest, "election": election, "magnitude": magnitude, "kind": kind, "options": []},
            )
            if option is not None:
                entry["options"].append(
                    {
                        "id": option,
                        "kind": option_kind,
                        "words": _words(label or ""),
                        "votes": votes,
                        "won": won,
                        "nested": nested,
                        "person": person,
                    }
                )
        others: dict[UUID, list[str]] = {}
        for contest, pipeline in conn.execute(
            """select distinct r.contest_id, c.pipeline from result_set r join changeset c on c.id = r.changeset_id
               join external_id x on x.entity_id = r.contest_id and x.scheme = 'us.contest'
               where x.value like '%%:house:%%' and x.value ~ '^1[78][0-9]{2}' and left(x.value, 4)::int < 1824
                 and c.pipeline not in ('us.thea', %s)""",
            [SOURCE],
        ).fetchall():
            others.setdefault(contest, []).append(pipeline)
        tally: Counter[str] = Counter()
        overlap = sorted(key for key, entry in held.items() if entry["contest"] in others)
        print(f"{len(overlap)} contests already hold results from other pipelines and are skipped: {overlap}")

        info = {row["record"]: row for row in house.iter_rows(named=True)}
        bystate: dict[str, list[str]] = {}
        for record, row in info.items():
            bystate.setdefault(row["postal"], []).append(record)
        pairs: list[tuple[tuple, str, str]] = []
        for key, entry in held.items():
            if entry["contest"] in others:
                continue
            year, postal = int(key[:4]), key.split(":")[1]
            named_options = [option for option in entry["options"] if option["kind"] == "candidate" and option["words"]]
            if not named_options:
                continue
            for record in bystate.get(postal, []):
                if abs(info[record]["year"] - year) > 1:
                    continue
                printed = [(_words(line["name"]), line) for line in lines.get(record, [])]
                found, exact, drift, winners = 0, 0, [], True
                for want in named_options:
                    best = max(printed, key=lambda pair, words=want["words"]: _alike(words, pair[0]), default=None)
                    if best is None or not _alike(want["words"], best[0]):
                        winners = winners and not want["won"]
                        continue
                    found += 1
                    winners = winners and (best[1]["elected"] or not want["won"])
                    if want["votes"] is not None and best[1]["total"] is not None:
                        exact += want["votes"] == best[1]["total"]
                        drift.append(abs(want["votes"] - best[1]["total"]) / max(want["votes"], best[1]["total"], 1))
                if found == len(named_options) and winners:
                    score = (exact, -(sum(drift) / len(drift) if drift else 1), -abs(info[record]["year"] - year))
                    pairs.append((score, key, record))
        matched: dict[str, str] = {}
        taken: set[str] = set()
        for _, key, record in sorted(pairs, reverse=True):
            if key not in matched and record not in taken:
                matched[key] = record
                taken.add(record)
        tally["THEA contests matched to an NNV record"] = len(matched)
        tally["THEA contests without a matching NNV record"] = (
            len(held) - len(matched) - len([key for key, entry in held.items() if entry["contest"] in others])
        )

        persons: dict[str, Counter[UUID]] = {}
        values: list[dict[str, object]] = []
        for key, record in sorted(matched.items()):
            entry = held[key]
            postal = key.split(":")[1]
            printed = lines.get(record, [])
            free = [option for option in entry["options"] if option["kind"] == "candidate" and option["words"]]
            picked: dict[str, UUID | None] = {}
            for line in sorted(printed, key=lambda line: -(line["total"] or 0)):
                words = _words(line["name"])
                if BLANK.match(line["name"]):
                    picked[line["candidate"]] = None
                    continue
                best = max(free, key=lambda option: _alike(words, option["words"]), default=None)
                if best is not None and _alike(words, best["words"]) and line["name"].lower() not in POOLED:
                    picked[line["candidate"]] = best["id"]
                    free.remove(best)
                    if line["name_id"] and best["person"] is not None:
                        persons.setdefault(line["name_id"], Counter())[best["person"]] += 1
            where = returns.get(record, {})
            local = {place: cell for place, cell in where.items() if place}
            if not local:
                tally["matched, no county or town returns in NNV"] += 1
                continue
            if any(option["nested"] for option in entry["options"]):
                tally["matched, THEA splits a candidate across lines"] += 1
                continue
            sums: dict[str, float] = {}
            for cell in local.values():
                for candidate, vote in cell.items():
                    sums[candidate] = sums.get(candidate, 0) + vote
            totals = {line["candidate"]: line["total"] for line in printed}
            if any(totals.get(candidate) is not None and vote > totals[candidate] for candidate, vote in sums.items()):
                tally["matched, units exceed NNV's printed totals"] += 1
                continue
            official = sum(option["votes"] or 0 for option in entry["options"])
            drifted = False
            for line in printed:
                option = next(
                    (option for option in entry["options"] if option["id"] == picked.get(line["candidate"])), None
                )
                if option is None or option["votes"] is None:
                    continue
                theirs = totals.get(line["candidate"])
                theirs = sums.get(line["candidate"], 0) if theirs is None else theirs
                if option["votes"] >= official * 0.05 and abs(theirs - option["votes"]) > max(
                    2, option["votes"] * 0.02
                ):
                    drifted = True
            if drifted:
                tally["matched, NNV's totals differ from THEA's by more than 2 percent"] += 1
                continue
            known = sum(total for total in totals.values() if total is not None)
            counted = sum(sums.values())
            result_set = cs.key(f"{SOURCE}.result_set", key)
            tally["attached" + (" (THEA has no votes)" if not official else "")] += 1
            tally[f"attached {postal}"] += 1
            if result_set not in cs.minted:
                continue
            pool = None
            if any(candidate not in picked for candidate in sums):
                pool = cs.key("us.option", f"{key}:others:nnv")
                cs.add("contest_option", id=pool, contest_id=entry["contest"], kind="others", ballot_label="Others")
            cs.add(
                "result_set",
                id=result_set,
                election_id=entry["election"],
                contest_id=entry["contest"],
                stage="official",
                kind="counted",
                status="active",
                source_id=source,
                is_default=False,
                completeness=round(min(counted / known, 1), 4) if known else None,
            )
            cs.add(
                "citation",
                entity_id=result_set,
                source_id=source,
                locator={"file": info[record]["file"], "election_id": record, "handle": info[record]["handle"]},
            )
            for place, cell in local.items():
                spot = unit(postal, place)
                merged: dict[tuple[UUID | None, int], float] = {}
                for candidate, vote in cell.items():
                    option = picked.get(candidate, pool) if candidate in picked else pool
                    measure = 6 if candidate in picked and picked[candidate] is None else 1
                    merged[(option, measure)] = merged.get((option, measure), 0) + vote
                for (option, measure), vote in merged.items():
                    values.append(
                        {
                            "result_set_id": str(result_set),
                            "unit_id": str(spot),
                            "option_id": None if option is None else str(option),
                            "channel": 0,
                            "measure": measure,
                            "count_round": 0,
                            "flags": 0,
                            "value": vote,
                        }
                    )

        groups: dict[tuple[str, str, bool], list[str]] = {}
        for record, row in info.items():
            seat = ", ".join(part for part in (row["label"] or "").split(", ")[1:] if not part.startswith("Ballot "))
            groups.setdefault((row["postal"], seat, row["type"] == "Special"), []).append(record)
        chains: list[list[str]] = []
        for members in groups.values():
            members.sort(
                key=lambda record: (info[record]["year"], info[record]["month"] or 0, info[record]["ballot"] or 1)
            )
            started: list[list[str]] = []
            for record in members:
                ballot = info[record]["ballot"] or 1
                previous = next(
                    (
                        chain
                        for chain in reversed(started)
                        if (info[chain[-1]]["ballot"] or 1) == ballot - 1
                        and info[record]["year"] - info[chain[-1]]["year"] <= 1
                    ),
                    None,
                )
                if ballot > 1 and previous is not None:
                    previous.append(record)
                else:
                    started.append([record])
            chains += started
        thea_seats: dict[tuple[str, int], list[tuple[int, bool, list[list[str]]]]] = {}
        for key, entry in held.items():
            stamp, postal, _, district, *rest = key.split(":")
            thea_seats.setdefault((postal, int(district)), []).append(
                (int(stamp[:4]), bool(rest), [option["words"] for option in entry["options"] if option["won"]])
            )
        bioguides: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'bioguide'").fetchall()
        )
        elections: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.election'").fetchall()
        )
        districts: dict[str, list[tuple[UUID, UUID, str, Range[date]]]] = {}
        for code, area, place_unit, text, valid in conn.execute(
            """select u.code, u.area_id, u.id, n.text, u.valid from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'official'
               where u.scheme_code = 'us.cd'"""
        ).fetchall():
            districts.setdefault(code, []).append((area, place_unit, text, valid))
        plans: list[tuple[UUID, date, date | None]] = conn.execute(
            "select id, valid_from, valid_to from boundary_set where scheme_code = 'us.cd'"
        ).fetchall()
        body = cs.key("us.body", "house")
        systems = {kind: cs.key("us.electoral_system", kind) for kind in ("fptp", "block")}
        if cs.minted & {body, *systems.values()}:
            raise ValueError("the House and its electoral systems must already exist")
        systems["majority"] = cs.key("us.electoral_system", "majority_trials")
        if systems["majority"] in cs.minted:
            cs.add(
                "electoral_system",
                id=systems["majority"],
                polity_id=us,
                rules={"formula": "repeated_ballot", "majorities": [{"fromRound": 1, "share": 0.5, "strict": True}]},
                rules_version=1,
            )
            named(systems["majority"], "Majority of votes cast, with repeated trials")

        def day_of(record: str) -> tuple[date, str, str]:
            row = info[record]
            if row["month"]:
                return date(row["year"], row["month"], 1), "month", f"{row['year']}-{row['month']:02d}"
            return date(row["year"], 1, 1), "year", str(row["year"])

        def seated(record: str) -> tuple[int, list[dict]] | None:
            row = info[record]
            day, year = day_of(record)[0], row["year"]
            if year % 2 == 1 and day <= date(year, 3, 4):
                order = [congress(date(year, 3, 4)), congress(date(year, 3, 4)) - 1]
            elif year % 2 == 1:
                order = [congress(day), congress(day) + 1]
            else:
                order = [congress(day) + 1, congress(day)]
            special = row["type"] == "Special"
            winners = [_words(line["name"]) for line in lines.get(record, []) if line["elected"]]
            for number in order:
                pool = [
                    member
                    for member in roster.get((number, row["postal"]), [])
                    if (member["start"] > opened.get(number, member["start"]) + timedelta(days=45)) == special
                    or (member["first"] and not special)
                ]
                hits = []
                for words in winners:
                    found = [member for member in pool if _alike(words, member["words"])]
                    if len({member["bioguide"] for member in found}) == 1:
                        hits.append(found[0])
                if winners and len(hits) == len(winners):
                    return number, hits
            return None

        def person_of(line: dict, postal: str, member: dict | None) -> UUID:
            if member is not None and member["bioguide"] in bioguides:
                return bioguides[member["bioguide"]]
            if line["name_id"] in persons:
                return persons[line["name_id"]].most_common(1)[0][0]
            if member is not None:
                person = cs.key("bioguide", member["bioguide"])
            else:
                person = cs.key(f"{SOURCE}.person", line["name_id"] or f"{postal}:{slug(line['name'])}")
            if person in cs.minted and person not in created:
                created.add(person)
                cs.add("person", id=person)
                named(person, line["name"])
            return person

        created: set[UUID] = set()
        listed: set[UUID] = set()
        used: set[str] = set()
        parties: dict[str, UUID] = {}
        stamps = {key for key in elections} | {
            f"{day_of(chain[0])[2]}:{info[chain[0]]['postal']}"
            for chain in chains
            if not any(record in taken for record in chain)
        }
        for chain in chains:
            postal = info[chain[0]]["postal"]
            special = info[chain[0]]["type"] == "Special"
            at_large = info[chain[0]]["role_scope"] == "State"
            hit = next(((index, found) for index, record in enumerate(chain) if (found := seated(record))), None)
            thea_index = next((index for index, record in enumerate(chain) if record in taken), None)
            if thea_index is not None:
                tally["NNV trials before a THEA contest's trial, not added"] += thea_index
                tally["NNV trials after a THEA contest's trial, not added"] += len(chain) - thea_index - 1
                continue
            if hit is None:
                tally["NNV races whose winner is not on the roster, not added"] += 1
                continue
            number, members = hit[1]
            district = 0 if at_large else members[0]["district"]
            tail = ", ".join(
                part
                for part in (info[chain[0]]["label"] or "").split(", ")[1:]
                if not part.startswith(("District ", "Ballot ")) and part != "At-Large"
            )
            year = info[chain[0]]["year"]
            winner_words = [
                _words(line["name"]) for record in chain for line in lines.get(record, []) if line["elected"]
            ]
            clash = any(
                abs(other - year) <= 1
                and (flag == special or any(_alike(one, two) for one in winner_words for two in won))
                for other, flag, won in thea_seats.get((postal, district), [])
            )
            if clash:
                tally["NNV races THEA holds without reconciling, not added"] += 1
                continue
            seats = (
                max(len(winner_words), 1)
                if special or tail
                else len(
                    [
                        member
                        for member in roster.get((number, postal), [])
                        if member["first"] and member["district"] == district
                    ]
                )
                or 1
            )
            pool_members = roster.get((number, postal), [])
            unit_id, area, place, fips = states[postal]
            start = date(1789 + 2 * (number - 1), 3, 4)
            umbrella = elections.get(f"congress-{number}")
            if umbrella is None:
                raise ValueError(f"no THEA umbrella election for the {number}th Congress")
            previous: UUID | None = None
            filled = 0
            first_election: UUID | None = None
            for round_number, record in enumerate(chain, start=1):
                row = info[record]
                day, precision, stamp = day_of(record)
                key = f"{stamp}:{postal}:house:{district}" + (":special" if special else "")
                key += f":{slug(tail)}" if tail else ""
                key += f":ballot{round_number}" if round_number > 1 else ""
                if key in used:
                    tally["NNV races duplicating another NNV race, not added"] += 1
                    break
                used.add(key)
                if round_number == 1 or f"{stamp}:{postal}" in stamps:
                    election_key, kind, part = f"{stamp}:{postal}", "general", "general"
                else:
                    election_key, kind, part = f"{stamp}:{postal}:runoff", "runoff", "runoff"
                election = cs.key("us.election", election_key)
                if election in cs.minted and election_key not in elections:
                    elections[election_key] = election
                    cs.add(
                        "election",
                        id=election,
                        polity_id=cs.polity(f"US-{postal}"),
                        parent_id=umbrella if kind == "general" else first_election,
                        kind=kind,
                        date_from=day,
                        date_to=day,
                        date_precision=precision,
                        status="held",
                    )
                    when = f"{day:%B %Y}" if precision == "month" else str(day.year)
                    named(election, f"{when} {place} {kind} election")
                    cs.add("slug", path=f"us/{postal.lower()}/{stamp}-{part}", entity_id=election)
                first_election = first_election or election
                contest = cs.key("us.contest", key)
                if contest not in cs.minted:
                    tally["NNV trials already on file"] += 1
                    previous = contest
                    continue
                on = day if special else start
                plan = next((id for id, begin, end in plans if begin <= on and (end is None or on < end)), None)
                matches = [entry for entry in districts.get(f"{fips}{district:02d}", []) if on in entry[3]]
                district_area, district_unit, title_place = area, unit_id, f"{place} at large"
                if len(matches) == 1:
                    district_area, district_unit, title_place, _ = matches[0]
                elif district:
                    title_place = f"{place}'s district {district}"
                magnitude = max(seats - filled, 1)
                elected = [line for line in lines.get(record, []) if line["elected"]]
                filled += len(elected)
                cs.add(
                    "contest",
                    id=contest,
                    election_id=election,
                    kind="body_seats",
                    body_id=body,
                    district_area_id=district_area,
                    seat_id=None,
                    term="remainder" if special else "full",
                    boundary_set_id=plan,
                    magnitude=magnitude,
                    round_number=round_number,
                    previous_round_id=previous,
                    electoral_system_id=systems[
                        "majority" if postal in MAJORITY else "block" if magnitude > 1 else "fptp"
                    ],
                    default_result_kind="counted",
                )
                trial = f", {TRIALS[round_number - 1]} trial" if round_number > 1 else ""
                named(
                    contest,
                    f"{day.year} U.S. House {'special election' if special else 'election'} in {title_place}"
                    + (f" ({tail[0].lower()}{tail[1:]})" if tail else "")
                    + trial,
                )
                cs.add(
                    "slug",
                    path=f"us/{postal.lower()}/{stamp}-{part}/{f'house-{district}' if district else 'house-at-large'}"
                    + ("-special" if special else "")
                    + (f"-{slug(tail)}" if tail else "")
                    + (f"-trial-{round_number}" if round_number > 1 else ""),
                    entity_id=contest,
                )
                previous = contest
                tally[f"added {postal}"] += 1
                tally["added"] += 1
                options: dict[str, UUID | None] = {}
                winners: list[UUID] = []
                for line in lines.get(record, []):
                    if BLANK.match(line["name"]):
                        options[line["candidate"]] = None
                        continue
                    if line["name"].lower() in POOLED or not _words(line["name"]):
                        option = cs.key("us.option", f"{key}:others")
                        if option in cs.minted and option not in options.values():
                            cs.add(
                                "contest_option", id=option, contest_id=contest, kind="others", ballot_label="Others"
                            )
                        options[line["candidate"]] = option
                        continue
                    found = [member for member in pool_members if _alike(_words(line["name"]), member["words"])]
                    member = found[0] if line["elected"] and len({one["bioguide"] for one in found}) == 1 else None
                    person = person_of(line, postal, member)
                    option = cs.key("us.option", f"{key}:{person}")
                    options[line["candidate"]] = option
                    if option not in cs.minted or option in listed:
                        continue
                    listed.add(option)
                    affiliation = line["affiliation"]
                    party = None
                    if affiliation is not None and affiliation not in UNAFFILIATED | {"supported by both parties"}:
                        party_key = NATIONAL.get(affiliation, f"{postal}:{slug(affiliation)}")
                        party = cs.key("us.party", party_key)
                        if party in cs.minted and party_key not in parties:
                            cs.add("party", id=party, kind="party", polity_id=cs.polity(f"US-{postal}"))
                            named(party, affiliation)
                        parties[party_key] = party
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=party,
                        ballot_label=line["name"],
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=line["name"],
                        is_independent=affiliation == "Independent",
                        is_incumbent=None,
                    )
                    if line["elected"]:
                        winners.append(option)
                totals = {line["candidate"]: line["total"] for line in lines.get(record, [])}
                local = {place_key: cell for place_key, cell in returns.get(record, {}).items() if place_key}
                sums: dict[str, float] = {}
                for cell in local.values():
                    for candidate, vote in cell.items():
                        sums[candidate] = sums.get(candidate, 0) + vote
                whole = (
                    bool(local)
                    and all(totals.get(candidate) == vote for candidate, vote in sums.items())
                    and all(total is None or total == sums.get(candidate, 0) for candidate, total in totals.items())
                )
                grains: list[tuple[UUID, dict[tuple[tuple[str, str], ...], dict[str, float]], bool]] = []
                default = cs.key(f"{SOURCE}.result_set", key)
                if whole:
                    grains.append((default, local, True))
                else:
                    printed = totals if None not in totals.values() else {}
                    grains.append((default, {(): printed} if printed else {}, True))
                    if local and not any(
                        totals.get(candidate) is not None and vote > totals[candidate]
                        for candidate, vote in sums.items()
                    ):
                        grains.append((cs.key(f"{SOURCE}.result_set", f"{key}:units"), local, False))
                    elif local:
                        tally["added, units exceed NNV's printed totals (contest totals only)"] += 1
                known = sum(total for total in totals.values() if total is not None)
                for result_set, places, is_default in grains:
                    counted = sum(vote for cell in places.values() for vote in cell.values())
                    cs.add(
                        "result_set",
                        id=result_set,
                        election_id=election,
                        contest_id=contest,
                        stage="official",
                        kind="counted",
                        status="active",
                        source_id=source,
                        is_default=is_default,
                        completeness=round(min(counted / known, 1), 4) if known else None if counted else 0,
                    )
                    cs.add(
                        "citation",
                        entity_id=result_set,
                        source_id=source,
                        locator={"file": row["file"], "election_id": record, "handle": row["handle"]},
                    )
                    if is_default:
                        for option in winners:
                            cs.add("outcome", result_set_id=result_set, option_id=option, kind="elected", seats=1)
                        if precision != "day":
                            cs.add(
                                "note",
                                entity_id=result_set,
                                kind="caveat",
                                body_md=MONTH_NOTE if precision == "month" else YEAR_NOTE,
                            )
                    for place_key, cell in places.items():
                        spot = unit(postal, place_key) if place_key else district_unit
                        merged: dict[tuple[UUID | None, int], float] = {}
                        for candidate, vote in cell.items():
                            if candidate not in options:
                                pool = cs.key("us.option", f"{key}:others")
                                if pool in cs.minted and pool not in options.values():
                                    cs.add(
                                        "contest_option",
                                        id=pool,
                                        contest_id=contest,
                                        kind="others",
                                        ballot_label="Others",
                                    )
                                options[candidate] = pool
                            option = options[candidate]
                            measure = 6 if option is None else 1
                            merged[(option, measure)] = merged.get((option, measure), 0) + vote
                        for (option, measure), vote in merged.items():
                            values.append(
                                {
                                    "result_set_id": str(result_set),
                                    "unit_id": str(spot),
                                    "option_id": None if option is None else str(option),
                                    "channel": 0,
                                    "measure": measure,
                                    "count_round": 0,
                                    "flags": 0,
                                    "value": vote,
                                }
                            )

        for what, count in sorted(tally.items()):
            print(f"{what}: {count}")
        if values:
            cs.frame(
                "result_value",
                pl.DataFrame(values, infer_schema_length=None)
                .group_by("result_set_id", "unit_id", "option_id", "channel", "measure", "count_round", "flags")
                .agg(pl.col("value").sum()),
            )
        return cs.write()
