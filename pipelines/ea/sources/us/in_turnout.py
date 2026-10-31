import difflib
import re
import subprocess
from datetime import UTC, datetime
from html import unescape
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.in_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Indiana Election Division"
PAGE = "https://www.in.gov/sos/elections/voter-information/register-to-vote/voter-registration-and-turnout-statistics"
ROW = re.compile(r"^\s*([A-Z][A-Za-z .'&]+?)\s+([\d,]+)\s+([\d,]+)\s+[\d.]+\s*%")


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", name.lower())


def listing() -> dict[tuple[int, str], str]:
    html = raw.cached(SOURCE, PAGE).path.read_text(errors="replace")
    files = {}
    for href, label in re.findall(r"(?s)<a [^>]*href=\"(/dA/[^\"]+)\"[^>]*>(.*?)</a>", html):
        match = re.match(
            r"(\d{4})\s+(Primary|General) Election",
            re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", "", label))).strip(),
        )
        if match and int(match[1]) % 2 == 0 and int(match[1]) < 2026:
            files["https://www.in.gov" + unescape(href)] = (int(match[1]), match[2].lower())
    return {v: k for k, v in files.items()}


def fetch() -> None:
    try:
        raw.cached(SOURCE, PAGE)
    except LookupError:
        raw.fetch(SOURCE, PAGE, suffix=".html")
    for address in listing().values():
        try:
            raw.cached(SOURCE, address)
            continue
        except LookupError:
            pass
        response = httpx.get(address, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=120)
        response.raise_for_status()
        raw.store(
            SOURCE,
            address,
            response.content,
            suffix=".pdf",
            retrieved_at=datetime.now(UTC),
            media_type="application/pdf",
        )


def parse() -> None:
    with connect() as conn:
        codes = {
            plain(name): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '18%'"""
            )
        }
    rows = []
    for (year, stage), address in sorted(listing().items()):
        text = subprocess.run(
            ["pdftotext", "-layout", str(raw.cached(SOURCE, address).path), "-"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        found = {}
        state = None
        for line in text.replace("\x0c", "\n").splitlines():
            match = ROW.match(line)
            if match is None:
                continue
            name = plain(re.sub(r"(?i) county$", "", match[1]))
            close = [name] if name in codes else difflib.get_close_matches(name, list(codes), n=1, cutoff=0.8)
            registered, voters = int(match[2].replace(",", "")), int(match[3].replace(",", ""))
            if name in ("indiana", "stateofindiana", "statewide", "total", "statetotal", "statetotals"):
                state = (registered, voters)
                continue
            if not close or registered == 0 or voters > registered or codes[close[0]] in found:
                if "total" not in name and not name.startswith("state"):
                    print(f"{year} {stage}: skipped {match[1]!r} {registered} {voters}")
                continue
            found[codes[close[0]]] = (registered, voters)
        print(
            f"{year} {stage}: {len(found)} counties {sum(v[0] for v in found.values())}/{sum(v[1] for v in found.values())}"
        )
        got = (sum(v[0] for v in found.values()), sum(v[1] for v in found.values()))
        agrees = state is None or (
            abs(got[0] - state[0]) <= state[0] // 1000 and abs(got[1] - state[1]) <= state[1] // 1000
        )
        print(f"  state row {state} {'agrees' if agrees else 'DISAGREES'}")
        if len(found) >= 88 and agrees:
            for code, (registered, voters) in found.items():
                rows.append({"year": year, "stage": stage, "code": code, "registered": registered, "voters": voters})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    addresses = listing()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Indiana's registered voters and ballots cast by county for the primaries and generals of 1990–2024, "
            "from the Election Division's voter registration and turnout statistics",
            polity="US",
        )
        elections: dict[tuple[int, str], list[tuple[str, str]]] = {}
        for value, entity_id, kind, start in conn.execute(
            """select x.value, x.entity_id, e.kind, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':IN(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
        ):
            if (kind == "general" and start.month == 11) or (kind == "primary" and start.month in (5, 6)):
                elections.setdefault((start.year, kind), []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Voter Registration and Turnout Statistics"
        source = cs.source(PUBLISHER, title)
        if source in cs.minted:
            cs.add(
                "source",
                id=source,
                kind="official_body",
                publisher=PUBLISHER,
                title=title,
                url=PAGE,
                license="Public record",
            )
        for (year, stage), rows in frame.group_by("year", "stage", maintain_order=True):
            found = elections.get((year, stage), [])
            if len(found) != 1:
                print(f"{year} {stage}: {len(found)} elections")
                continue
            value_key, election_id = found[0]
            cs.artifact(raw.cached(SOURCE, addresses[(year, stage)]), source)
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
                completeness=round(rows.height / 92, 4),
            )
            for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                for measure, value in ((2, registered), (3, voters)):
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
