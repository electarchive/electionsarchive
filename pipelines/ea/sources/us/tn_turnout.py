import difflib
import re
import subprocess
import tempfile
import time
from datetime import UTC, date, datetime
from pathlib import Path

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.store import DATA, connect

SOURCE = "us.tn_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Tennessee Secretary of State"
WAYBACK = "https://web.archive.org/web/20251231205750id_/"
BASE = "https://web.archive.org/web/20241212142335id_/http://sharetngov.tnsosfiles.com/sos/election/data/turnout/"
FILES = {
    date(1994, 8, 4): "1994-08.pdf",
    date(1994, 11, 8): "1994-11.pdf",
    date(1996, 8, 1): "1996-08.pdf",
    date(1996, 11, 5): "1996-11.pdf",
    date(1998, 8, 6): "1998-08.pdf",
    date(1998, 11, 3): "1998-11.pdf",
    date(2000, 8, 3): "2000-08.pdf",
    date(2000, 11, 7): "2000-11.pdf",
    date(2002, 8, 1): "2002-08.pdf",
    date(2002, 11, 5): "2002-11.pdf",
    date(2004, 2, 10): "2004-02.pdf",
    date(2004, 8, 5): "2004-08.pdf",
    date(2004, 11, 2): "2004-11.pdf",
    date(2006, 8, 3): "2006-08.pdf",
    date(2006, 11, 7): "2006-11.pdf",
    date(2008, 2, 5): "2008-02.pdf",
    date(2008, 8, 7): "2008-08.pdf",
    date(2008, 11, 4): "2008-11.pdf",
    date(2010, 8, 5): "2010-08.pdf",
    date(2010, 11, 2): "2010-11.pdf",
    date(2012, 3, 6): "2012_03%20Turnout.pdf",
    date(2012, 8, 2): "2012_08%20Turnout.pdf",
    date(2012, 11, 6): "2012_11%20Turnout.pdf",
    date(2014, 8, 7): "20140807.pdf",
    date(2014, 11, 4): "20141104_Turnout.pdf",
    date(1996, 3, 12): WAYBACK + "http://sharetngov.tnsosfiles.com/sos/election/data/turnout/1996-03.pdf",
    date(2000, 3, 14): WAYBACK + "http://sharetngov.tnsosfiles.com/sos/election/data/turnout/2000-03.pdf",
    date(2016, 3, 1): WAYBACK + "https://sos-tn-gov-files.s3.amazonaws.com/2016%20March%20PPP%20Turnout.pdf",
    date(2016, 8, 4): WAYBACK + "https://sos-tn-gov-files.s3.amazonaws.com/2016_August_PPP_Turnout.pdf",
    date(2016, 11, 8): WAYBACK + "https://sos-tn-gov-files.s3.amazonaws.com/2016_November_PPP_Turnout.pdf",
    date(2022, 8, 4): WAYBACK + "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/2022_August_Turnout.pdf",
    date(2024, 11, 5): WAYBACK + "https://sos-prod.tnsosgovfiles.com/s3fs-public/document/2024%20November%20Turnout%20Report.pdf",
}
FILES = {day: name if name.startswith("http") else BASE + name for day, name in FILES.items()}
ROW = re.compile(r"^\s*([A-Za-z][A-Za-z .'-]*?):?\s+([\d,]+)\s+([\d,]+)\s+([\d.]+)%")


def plain(name: str) -> str:
    return re.sub(r"[^a-z]", "", re.sub(r"(?i) county$", "", name).lower())


def fetch() -> None:
    for name in FILES.values():
        address = name
        try:
            raw.cached(SOURCE, address)
            continue
        except LookupError:
            pass
        for attempt in range(6):
            try:
                response = httpx.get(address, headers={"User-Agent": "Mozilla/5.0"}, follow_redirects=True, timeout=120)
            except httpx.HTTPError:
                time.sleep(30 * (attempt + 1))
                continue
            if response.status_code == 200 and response.content[:4] == b"%PDF":
                raw.store(SOURCE, address, response.content, suffix=".pdf", retrieved_at=datetime.now(UTC), media_type="application/pdf")
                break
            time.sleep(15 * (attempt + 1))
        else:
            print(f"{name}: not fetched")
        time.sleep(3)


def parse() -> None:
    with connect() as conn:
        codes = {
            plain(name): code
            for code, name in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and u.code like '47%'"""
            )
        }
    rows = []
    for day, name in FILES.items():
        try:
            path = raw.cached(SOURCE, name).path
        except LookupError:
            continue
        text = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, text=True, check=True).stdout
        scanned = not text.strip()
        if scanned:
            with tempfile.TemporaryDirectory() as tmp:
                subprocess.run(["pdftoppm", "-r", "300", "-gray", str(path), f"{tmp}/p"], check=True)
                text = "\n".join(
                    subprocess.run(["tesseract", str(image), "-", "--psm", "6"], capture_output=True, text=True, check=True).stdout
                    for image in sorted(Path(tmp).glob("p-*.pgm"))
                )
        found = {}
        total = None
        for line in text.replace("\x0c", "\n").splitlines():
            match = ROW.match(line)
            if match is None:
                continue
            key = plain(match[1])
            values = (int(match[2].replace(",", "")), int(match[3].replace(",", "")))
            if key == "total":
                total = values
                continue
            close = [key] if key in codes else difflib.get_close_matches(key, list(codes), n=1, cutoff=0.85)
            if scanned and abs(round(values[1] / values[0] * 10000) - round(float(match[4]) * 100)) > 1:
                continue
            if close and codes[close[0]] not in found and values[1] <= values[0]:
                found[codes[close[0]]] = values
        got = (sum(v[0] for v in found.values()), sum(v[1] for v in found.values()))
        ok = len(found) == 95 and (total is None or (abs(got[0] - total[0]) <= total[0] // 500 and abs(got[1] - total[1]) <= total[1] // 500))
        print(f"{day}: {len(found)} counties {got} vs {total} {'ok' if ok else 'SKIPPED'}")
        if ok:
            for code, (registered, voters) in found.items():
                rows.append({"date": day, "code": code, "registered": registered, "voters": voters})
    PARSED.mkdir(parents=True, exist_ok=True)
    pl.DataFrame(rows).write_parquet(PARSED / "counties.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Tennessee's registered voters and total votes cast by county for its August and November elections and presidential primaries 1994–2014, "
            "from the Secretary of State's statistical analyses of voter turnout",
            polity="US",
        )
        elections = {
            start: (value, entity_id)
            for value, entity_id, start in conn.execute(
                """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
                   where x.scheme = 'us.election' and x.value ~ ':TN(:[a-z]+)?$' and e.kind in ('general', 'primary')"""
            )
        }
        held = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and is_default and status <> 'withdrawn'"
            )
        }
        title = "Statistical Analysis of Voter Turnout"
        source = cs.source(PUBLISHER, title)
        published = {
            election_id
            for (election_id,) in conn.execute(
                "select election_id from result_set where contest_id is null and source_id = %s and status <> 'withdrawn'", (source,)
            )
        }
        if source in cs.minted:
            cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url="https://sos.tn.gov/elections/statistics", license="Public record")
        for (day,), rows in frame.group_by("date", maintain_order=True):
            if day not in elections:
                print(f"{day}: no election")
                continue
            value_key, election_id = elections[day]
            if election_id in published:
                continue
            cs.artifact(raw.cached(SOURCE, FILES[day]), source)
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
                completeness=round(rows.height / 95, 4),
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
