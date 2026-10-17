import csv
import io
import re
import time
from itertools import takewhile
from pathlib import Path
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.me_canvass"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
WAYBACK = "https://web.archive.org/web/{}id_/{}"
SITE = "http://www.maine.gov/sos/cec/elec/"
PUBLISHER = "Maine Secretary of State"
TITLE = "Official tabulations by town"
FILES = {
    ("1990-11-06", "governor"): ("20080206210940", "1990g/gv-90g.txt"),
    ("1990-11-06", "senate"): ("20080206211000", "1990g/us-90g.txt"),
    ("1992-11-03", "president"): ("20080206211414", "1992g/pr-92g.txt"),
    ("1994-11-08", "governor"): ("20030912063023", "1994g/gen94g.txt"),
    ("1994-11-08", "senate"): ("20030912064918", "1994g/gen94s.txt"),
    ("2010-11-02", "governor"): ("20150809042630", "results/2010-11/governor.txt"),
}
NAMES = {"ADAM": "Andrew Adam"}
STATEWIDE = "{} were reported statewide, not by town, and are not in the county breakdown."


def fetch() -> None:
    for stamp, path in FILES.values():
        for attempt in range(6):
            try:
                raw.fetch(SOURCE, WAYBACK.format(stamp, SITE + path), suffix=".txt")
                break
            except httpx.TransportError, httpx.HTTPStatusError:
                if attempt == 5:
                    raise
                time.sleep(4 + 30 * attempt)


def parse() -> None:
    rows: list[dict[str, object]] = []
    for (day, office), (stamp, path) in FILES.items():
        text = raw.cached(SOURCE, WAYBACK.format(stamp, SITE + path)).path.read_bytes().decode("latin-1")
        found: list[tuple[str, str, str | None, str]] = []
        if day.startswith("2010"):
            lines = [line.split("|") for line in text.splitlines()]
            for line in lines[4:]:
                if len(line) > 5 and line[0] == "Governor":
                    found.extend(
                        (line[2], header.strip(), party.strip(), votes)
                        for header, party, votes in zip(lines[0][5:], lines[2][5:], line[5:], strict=False)
                        if header.strip() and header.strip() != "BLANK"
                    )
        elif day.startswith("1994"):
            for record in csv.DictReader(io.StringIO(text)):
                count = 1
                while f"name{count}" in record:
                    if record[f"name{count}"]:
                        found.append(
                            (record["county"], record[f"name{count}"], record[f"party{count}"], record[f"vote{count}"])
                        )
                    count += 1
                found.append((record["county"], "OTHERS", None, record["other"]))
        else:
            for record in csv.reader(io.StringIO(text)):
                fields = [field.strip() for field in record]
                if len(fields) < 6:
                    continue
                values = list(takewhile(lambda field: re.fullmatch(r"\d*", field) is not None, fields[4:]))
                names = [field for field in fields[4 + len(values) :] if field]
                found.extend(
                    (fields[2], header, None, values[index] if index < len(values) else "")
                    for index, header in enumerate([*names, "OTHERS"])
                )
        rows.extend(
            {"day": day, "office": office, "county": county, "header": header, "party": party, "votes": int(votes or 0)}
            for county, header, party, votes in found
        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows, infer_schema_length=None)
        .with_columns(pl.col("county").str.strip_chars().str.to_uppercase())
        .group_by("day", "office", "county", "header", maintain_order=True)
        .agg(pl.col("party").first(), pl.col("votes").sum())
        .write_parquet(PARSED / "lines.parquet")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "lines.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Maine's governor, U.S. Senate, and president by county 1990–1994 and governor 2010, "
            "from the Secretary of State's town tabulations",
            polity="US",
        )

        def plain(text: str) -> str:
            return re.sub(r"[^a-z]", "", text.lower())

        def surname(text: str) -> str:
            text = re.sub(r"\([^)]*\)|\"[^\"]*\"", " ", text)
            if "," in text:
                text = text.split(",")[0]
            words = [word for word in text.split() if plain(word) not in {"jr", "sr", "ii", "iii", "iv"}]
            return plain(words[-1]) if words else ""

        source = cs.source(PUBLISHER, TITLE)
        cs.add(
            "source",
            id=source,
            kind="official_body",
            publisher=PUBLISHER,
            title=TITLE,
            url="https://www.maine.gov/sos/cec/elec/results/",
            license="Public record",
        )
        for stamp, path in FILES.values():
            cs.artifact(raw.cached(SOURCE, WAYBACK.format(stamp, SITE + path)), source)
        counties: dict[str, str] = {
            plain(text)[:3].upper(): str(unit)
            for text, unit in conn.execute(
                """select n.text, u.id from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '23%%'"""
            ).fetchall()
        }
        if len(counties) != 16:
            raise ValueError("Maine needs 16 counties")
        missing = sorted({county for county in frame["county"] if county and county not in counties})
        if missing:
            raise ValueError(f"unmatched counties: {missing}")
        people: dict[str, set[UUID]] = {}
        for person, text in conn.execute(
            """select distinct cd.person_id, n.text from candidacy cd
               join contest_option o on o.id = cd.option_id join contest c on c.id = o.contest_id
               join election e on e.id = c.election_id join polity p on p.id = e.polity_id and p.iso_code = 'US-ME'
               join name n on n.entity_id = cd.person_id and n.is_preferred"""
        ).fetchall():
            people.setdefault(plain(re.sub(r"\b[A-Z]\. ", "", text)), set()).add(person)
            people.setdefault(plain(text), set()).add(person)
        parties = {"dem": cs.key("us.party", "democratic-party"), "rep": cs.key("us.party", "republican-party")}
        if cs.minted & set(parties.values()):
            raise ValueError("the national parties must exist")
        made: set[UUID] = set()
        contests: dict[str, UUID] = dict(
            conn.execute("select value, entity_id from external_id where scheme = 'us.contest' and value ~ ':ME:'")
        )
        for (day, office), lines in frame.group_by("day", "office", maintain_order=True):
            key = f"{day}:ME:{office}"
            contest = contests[key]
            election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
            result_set = cs.key(f"{SOURCE}.result_set", key)
            options = conn.execute(
                """select id, kind, ballot_label from contest_option
                   where contest_id = %s and parent_option_id is null""",
                [contest],
            ).fetchall()
            columns = (
                "id",
                "election_id",
                "contest_id",
                "stage",
                "kind",
                "status",
                "source_id",
                "as_of",
                "completeness",
                "supersedes_id",
            )
            for row in conn.execute(
                f"select {', '.join(columns)} from result_set where contest_id = %s and is_default", [contest]
            ).fetchall():
                cs.add("result_set", **dict(zip(columns, row, strict=True)), is_default=False)
            outcomes = conn.execute(
                """select o.option_id, o.kind, o.seats from outcome o
                   join result_set rs on rs.id = o.result_set_id and rs.is_default where rs.contest_id = %s""",
                [contest],
            ).fetchall()
            on_file = dict(
                conn.execute(
                    """select o.id, sum(v.value)::bigint from result_value v
                       join result_set rs on rs.k = v.result_set_k and rs.is_default
                       join contest_option o on o.k = v.option_k
                       where rs.contest_id = %s and v.measure = 1 group by 1""",
                    [contest],
                ).fetchall()
            )
            statewide = lines.filter(pl.col("county") == "")
            lines = lines.filter(pl.col("county") != "")
            picked: dict[str, UUID] = {}
            totals: dict[UUID, int] = {}
            pool = None
            labels = dict(lines.select("header", "party").unique("header").iter_rows())
            for header, votes in lines.group_by("header", maintain_order=True).agg(pl.col("votes").sum()).iter_rows():
                writein = header == "OTHERS" or "write-in" in header.lower()
                matches = (
                    []
                    if writein
                    else [
                        option
                        for option, kind, text in options
                        if kind != "write_in" and surname(text or "") == surname(header)
                    ]
                )
                if len(matches) > 1:
                    raise ValueError(f"{key}: {len(matches)} options for {header}")
                if matches:
                    option = matches[0]
                elif writein or votes < 0.005 * lines["votes"].sum():
                    if not votes:
                        continue
                    if pool is None:
                        generic = [option for option, kind, _ in options if kind in ("write_in", "others")]
                        pool = generic[0] if len(generic) == 1 else cs.key("us.option", f"{key}:write_in:me_canvass")
                        if pool in cs.minted:
                            cs.add(
                                "contest_option", id=pool, contest_id=contest, kind="write_in", ballot_label="Others"
                            )
                    option = pool
                else:
                    shown = NAMES.get(header) or " ".join(reversed([part.strip() for part in header.split(",", 1)]))
                    if " " not in shown:
                        raise ValueError(f"{key}: {header} is a surname only and matches no option")
                    known = people.get(plain(shown), set())
                    person = next(iter(known)) if len(known) == 1 else cs.key(f"{SOURCE}.person", f"ME:{slug(shown)}")
                    if person in cs.minted and person not in made:
                        made.add(person)
                        cs.add("person", id=person)
                        cs.add("name", entity_id=person, text=shown, lang="en", kind="official", is_preferred=True)
                    label = labels.get(header) or ""
                    option = cs.key("us.option", f"{key}:{person}")
                    cs.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="candidate",
                        party_id=parties.get(label[:3].lower()),
                        ballot_label=shown,
                    )
                    cs.add(
                        "candidacy",
                        id=cs.key("us.candidacy", f"{key}:{person}:head"),
                        person_id=person,
                        option_id=option,
                        role="head",
                        ballot_name=shown,
                        is_independent=label[:3].lower() not in parties,
                    )
                if option in on_file and abs(on_file[option] - votes - totals.get(option, 0)) > max(
                    10, on_file[option] // 200
                ):
                    print(f"{key}: {header} has {votes}, {on_file[option]} on file")
                totals[option] = totals.get(option, 0) + votes
                picked[header] = option
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
                completeness=1,
            )
            if statewide.height:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="caveat",
                    body_md=STATEWIDE.format(
                        "Overseas and military ballots ("
                        + ", ".join(
                            f"{header} {votes:,}"
                            for header, votes in statewide.select("header", "votes").iter_rows()
                            if votes
                        )
                        + ")"
                    ),
                )
            for (county, option), cells in (
                lines.filter(pl.col("header").is_in(list(picked)))
                .with_columns(option=pl.col("header").replace_strict({k: str(v) for k, v in picked.items()}))
                .group_by("county", "option")
                .agg(pl.col("votes").sum())
                .rows_by_key(["county", "option"])
                .items()
            ):
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=counties[county],
                    option_id=UUID(option),
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=cells[0][0],
                )
            if office == "president":
                for option, kind, seats in outcomes:
                    cs.add("outcome", result_set_id=result_set, option_id=option, kind=kind, seats=seats)
            else:
                winner = max((option for option in totals if option != pool), key=totals.__getitem__)
                cs.add("outcome", result_set_id=result_set, option_id=winner, kind="elected", seats=1)
        return cs.write()
