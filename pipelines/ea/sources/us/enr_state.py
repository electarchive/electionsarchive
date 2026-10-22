import json
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import portals, raw
from ea.changeset import Changeset
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.enr_state"
VERSION = "1"
HOST = "https://results.enr.clarityelections.com/"
PARSED = DATA / "parsed" / SOURCE
STATES = {"AL": "Alabama", "OK": "Oklahoma", "IA": "Iowa"}
FIPS = {"AL": "01", "OK": "40", "IA": "19"}
TITLE = "Election night reporting results by county"
CAVEAT = (
    "County votes are the final election night reporting file the state posted, which may predate the certified "
    "canvass; each candidate's county sum is within 2 percent of the official total."
)
OFFICES = {
    "president": r"\bPRESIDENT\b",
    "senate": r"UNITED STATES SENATOR|U\.? ?S\.? SENATOR",
    "house": r"(?:UNITED STATES|U\.? ?S\.?) REPRESENTATIVE",
    "governor": r"^(?:FOR )?GOVERNOR\b",
}
PARTIES = {"REP": "republican", "REPUBLICAN": "republican", "DEM": "democratic", "DEMOCRATIC": "democratic"}


def fetch() -> None:
    _, index = raw._index(SOURCE)
    cutoff = date.today() - timedelta(days=14)
    for st in STATES:
        status, body, _ = portals.get(f"{HOST}{st}/elections.json")
        if status != 200:
            print(f"{st}: {status}")
            continue
        for listed in json.loads(body):
            day = datetime.strptime(listed["Date"].split()[0], "%m/%d/%Y").date()
            if day > cutoff or any(word in listed["ElectionName"].upper() for word in ("TEST", "MOCK", "TRAINING")):
                continue
            if any(key.startswith(f"{HOST}{st}/{listed['EID']}/") for key in index):
                continue
            status, version, _ = portals.get(f"{HOST}{st}/{listed['EID']}/current_ver.txt")
            if status != 200:
                print(f"{st} {listed['EID']}: version {status}")
                continue
            link = f"{HOST}{st}/{listed['EID']}/{version.decode().strip()}/reports/detailxml.zip"
            status, content, _ = portals.get(link)
            if status != 200:
                print(f"{link}: {status}")
                continue
            raw.store(
                SOURCE,
                link,
                content,
                suffix=".zip",
                retrieved_at=datetime.now().astimezone(),
                media_type="application/zip",
            )
            print(f"{st} {day} {listed['ElectionName']}", flush=True)


def parse() -> None:
    with connect() as conn:
        counties = {
            (code[:2], re.sub(r"[^a-z]", "", text.lower().replace("saint ", "st "))): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.county' and left(u.code, 2) = any(%s)""",
                [list(FIPS.values())],
            ).fetchall()
        }
    rows = []
    for link in sorted(raw._index(SOURCE)[1]):
        st = link.removeprefix(HOST).split("/")[0]
        root = ElementTree.fromstring(zipfile.ZipFile(raw.cached(SOURCE, link).path).read("detail.xml"))
        if not re.fullmatch(r"\d+/\d+/(19|20)\d\d", root.findtext("ElectionDate") or ""):
            print(f"{link}: no election date, skipped")
            continue
        day = datetime.strptime(root.findtext("ElectionDate") or "", "%m/%d/%Y").date()
        for contest in root.iter("Contest"):
            text = " ".join((contest.get("text") or "").upper().split())
            office = next((name for name, pattern in OFFICES.items() if re.search(pattern, text)), None)
            if office is None or "DELEGATE" in text:
                continue
            party = next((PARTIES[word] for word in re.findall(r"[A-Z]+", text) if word in PARTIES), None)
            district = re.search(r"(?:DISTRICT|DIST)\D{0,3}(\d+)|(\d+)(?:ST|ND|RD|TH) CONGRESSIONAL", text)
            for choice in contest.iter("Choice"):
                for kind in choice.iter("VoteType"):
                    for place in kind:
                        name = re.sub(r"[^a-z]", "", (place.get("name") or "").lower().replace("saint ", "st "))
                        code = counties.get((FIPS[st], name))
                        if code is None:
                            raise ValueError(f"{link}: unmatched county {place.get('name')}")
                        rows.append(
                            {
                                "year": day.year,
                                "chamber": f"{office}:{party}" if party else office,
                                "day": day,
                                "special": False,
                                "state": st,
                                "district": int(district[1] or district[2]) if office == "house" and district else None,
                                "code": code,
                                "candidate": " ".join((choice.get("text") or "").split()).upper(),
                                "votes": int(place.get("votes") or 0),
                                "kind": "county",
                            }
                        )
    PARSED.mkdir(parents=True, exist_ok=True)
    (
        pl.DataFrame(rows, schema_overrides={"district": pl.Int16, "year": pl.Int32})
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by("year", "chamber", "day", "special", "state", "district", "code", "kind", "candidate", "measure")
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(source=pl.col("state"))
        .write_parquet(PARSED / "counties.parquet")
    )


def emit() -> Path:
    returns = pl.read_parquet(PARSED / "counties.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Federal and statewide contests by county from the states' election night reporting sites",
            polity="US",
        )
        sources = {}
        for st in sorted(set(returns["state"].to_list())):
            publisher = f"{STATES[st]} Secretary of State"
            sources[st] = cs.source(publisher, TITLE)
            if sources[st] in cs.minted:
                cs.add(
                    "source",
                    id=sources[st],
                    kind="official_body",
                    publisher=publisher,
                    title=TITLE,
                    url=f"{HOST}{st}/",
                    license="Public record",
                )
        for link in raw._index(SOURCE)[1]:
            st = link.removeprefix(HOST).split("/")[0]
            if st in sources:
                cs.artifact(raw.cached(SOURCE, link), sources[st])
        medsl.attach(cs, conn, returns, sources, strict=True)
        for row in cs.rows.get("result_set", []):
            if row["status"] == "active":
                row["stage"] = "preliminary"
                cs.add("note", entity_id=row["id"], kind="caveat", body_md=CAVEAT)
        return cs.write()
