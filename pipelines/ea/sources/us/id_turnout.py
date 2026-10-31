import html
import re
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.id_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Idaho Secretary of State"
BASE = "https://archive.sos.idaho.gov/ELECT/results/"
TITLE = "Voting Statistics by County"
PAGES = [
    (1994, "primary", None, "1994/primary/94PRVOT.HTM"),
    (1994, "general", None, "1994/general/94GNVOT.HTM"),
    (1996, "primary", None, "1996/primary/96PRVOT.HTM"),
    (1996, "general", None, "1996/general/96GNVOT.HTM"),
    (1998, "primary", None, "1998/primary/98PRVOT.HTM"),
    (1998, "general", None, "1998/general/98GNSTAT.HTM"),
    (2000, "primary", None, "2000/primary/vot_stat.htm"),
    (2000, "general", None, "2000/general/vot_stat.htm"),
    (2002, "primary", None, "2002/primary/cnty_vot.htm"),
    (2002, "general", None, "2002/general/cnty_vot.htm"),
    (2004, "primary", None, "2004/primary/cnty_votestat.htm"),
    (2006, "primary", None, "2006/primary/cnty_votestat.htm"),
    (2008, "primary", None, "2008/Primary/cnty_votestat.htm"),
    (2010, "primary", None, "2010/Primary/cnty_votestat.htm"),
    (2012, "primary", None, "2012/Primary/cnty_votestat.htm"),
    (2014, "primary", None, "2014/Primary/voting_statistics_by_county.html"),
    (2016, "primary", 3, "2016/PresidentialPrimary/voting_statistics_by_county.html"),
    (2016, "primary", 5, "2016/Primary/voting_statistics_by_county.html"),
    (2018, "primary", None, "2018/Primary/voting_statistics_by_county.html"),
]
COUNTIES_SQL = """
select u.code, n.text from reporting_unit u
join name n on n.entity_id = u.area_id and n.kind = 'short'
where u.scheme_code = 'us.county' and u.code like '16%'
"""


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def fetch() -> None:
    for *_, path in PAGES:
        raw.fetch(SOURCE, BASE + path, suffix=".html")


def parse() -> None:
    with connect() as conn:
        codes = {plain(name): code for code, name in conn.execute(COUNTIES_SQL)}
    rows = []
    for year, stage, month, path in PAGES:
        text = raw.cached(SOURCE, BASE + path).path.read_text(encoding="utf8", errors="replace")
        text = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "", text)
        text = re.sub(r"(?i)</tr>", "§", text)
        text = html.unescape(re.sub(r"<[^>]+>", "|", text))
        for line in text.split("§"):
            cells = [c.strip() for c in line.split("|") if c.strip()]
            if len(cells) < 4 or plain(cells[0]) not in codes:
                continue
            numbers = [int(c.replace(",", "")) for c in cells[1:] if re.fullmatch(r"[\d,]+", c)]
            if len(numbers) < 2:
                continue
            registered, voted = numbers[-2], numbers[-1]
            rows.append(
                {
                    "year": year,
                    "stage": stage,
                    "month": month,
                    "code": codes[plain(cells[0])],
                    "registered": registered,
                    "voted": voted,
                    "path": path,
                }
            )
    frame = pl.DataFrame(rows, infer_schema_length=None).filter(pl.col("voted") <= pl.col("registered"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "counties.parquet")
    print(
        frame.group_by("year", "stage", "month")
        .agg(pl.len(), pl.col("registered").sum(), pl.col("voted").sum())
        .sort("year", "stage")
    )


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Idaho's registered voters and ballots cast by county for its primary and general elections 1994–2002 "
            "and its primaries 2004–2018, from the Secretary of State's voting statistics",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str, int]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':ID(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            elections.setdefault((start.year, kind), []).append((value, entity_id, start.month))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        source = cs.source(PUBLISHER, TITLE)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=TITLE,
                url=BASE,
                license="Public record",
            )
        total = frame.select(pl.col("code").n_unique()).item()
        for (year, stage, month, path), rows in frame.group_by("year", "stage", "month", "path", maintain_order=True):
            found = [f for f in elections.get((year, stage), []) if month in (None, f[2])]
            if stage == "general" and len(found) > 1:
                found = [f for f in found if f[0][:4].isdigit() and f[0].count("-") == 2 and f[2] == 11]
            if year == 2000 and stage == "primary":
                found = [f for f in found if f[0].startswith("2000-05-23")]
            if len(found) != 1:
                print(f"{year} {stage} {month}: {len(found)} elections")
                continue
            value_key, election_id, _ = found[0]
            cs.artifact(raw.cached(SOURCE, BASE + path), source)
            result_set = cs.key(f"{SOURCE}.result_set", f"{value_key}:county")
            cs.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=None,
                stage="certified",
                kind="counted",
                status="active",
                source_id=source,
                is_default=election_id not in held,
                completeness=round(rows.height / total, 4),
            )
            for code, registered, voted in rows.select("code", "registered", "voted").iter_rows():
                for measure, value in ((2, registered), (3, voted)):
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=cs.unit("us.county", code),
                        option_id=None,
                        channel=0,
                        measure=measure,
                        count_round=0,
                        flags=0,
                        value=value,
                    )
        return cs.write()
