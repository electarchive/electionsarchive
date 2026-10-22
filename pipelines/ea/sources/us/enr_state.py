import json
import re
import zipfile
from datetime import date, datetime, timedelta
from pathlib import Path
from xml.etree import ElementTree

import polars as pl

from ea import portals, raw
from ea.changeset import Changeset, slug
from ea.sources.us import medsl
from ea.store import DATA, connect

SOURCE = "us.enr_state"
VERSION = "1"
HOST = "https://results.enr.clarityelections.com/"
PARSED = DATA / "parsed" / SOURCE
STATES = {"AL": "Alabama", "OK": "Oklahoma", "IA": "Iowa"}
FIPS = {"AL": "01", "OK": "40", "IA": "19", "GA": "13", "UT": "49", "VA": "51"}
EV = {"GA": ("georgia", "Georgia"), "UT": ("utah", "Utah"), "VA": ("virginia", "Virginia")}
API = "https://app.enhancedvoting.com/results/public/api/"
CDN = "https://app.enhancedvoting.com/cdn/results/"
TITLE = "Election night reporting results by county"
CAVEAT = (
    "County votes are the final election night reporting file the state posted, which may predate the certified "
    "canvass; each candidate's county sum is within 2 percent of the official total."
)
OFFICES = {
    "president": r"PRESIDENT OF THE U|^(?:FOR )?PRESIDENT\b",
    "senate": r"UNITED STATES SENAT|\bU\.? ?S\.? SENAT",
    "house": r"UNITED STATES REPRESENTATIVE|\bU\.? ?S\.? (?:HOUSE|REPRESENTATIVE)|^MEMBER,? HOUSE OF REPRESENTATIVES",
    "governor": r"^(?:FOR )?GOVERNOR\b",
    "statewide": r"^(?:FOR )?(?:LIEUTENANT GOVERNOR|SECRETARY OF STATE|ATTORNEY GENERAL|(?:STATE )?TREASURER|(?:STATE )?AUDITOR|"
    r"COMMISSIONER OF [A-Z]+|(?:STATE )?SCHOOL SUPERINTENDENT)\b",
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
    settled = date.today() - timedelta(days=120)
    for st, (tenant, _) in EV.items():
        status, body, _ = portals.get(f"{API}jurisdictions/{tenant}")
        if status != 200:
            print(f"{tenant}: {status}")
            continue
        for election in json.loads(body)["elections"]:
            link = f"{API}elections/{tenant}/{election['publicElectionId']}/data"
            day = date.fromisoformat(election["electionDate"][:10])
            if day > cutoff or link in index:
                continue
            status, body, _ = portals.get(link)
            if status != 200:
                print(f"{link}: {status}")
                continue
            data = json.loads(body)
            export = data["jurisdiction"].get("mediaExportPath")
            names = [" ".join(item["name"][0]["text"].upper().split()) for item in data["ballotItems"]]
            if not any(re.search(pattern, name) for name in names for pattern in OFFICES.values()):
                continue
            if not data["election"]["isOfficialResults"] and day > settled or not export:
                print(f"{link}: not official" if export else f"{link}: no export")
                continue
            status, content, _ = portals.get(f"{CDN}{export}")
            if status != 200:
                print(f"{CDN}{export}: {status}")
                continue
            now = datetime.now().astimezone()
            raw.store(
                SOURCE, f"{CDN}{export}", content, suffix=".json", retrieved_at=now, media_type="application/json"
            )
            raw.store(SOURCE, link, body, suffix=".json", retrieved_at=now, media_type="application/json")
            print(f"{st} {day} {election['publicElectionId']} {len(content) >> 20} MB", flush=True)


def parse() -> None:
    with connect() as conn:
        counties = {
            (code[:2], re.sub(r"[^a-z]", "", text.split(",")[0].lower().replace("&", "and"))): code
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u
                   join name n on n.entity_id = u.area_id and n.kind = 'official'
                   where u.scheme_code = 'us.county' and u.valid_to is null and left(u.code, 2) = any(%s)""",
                [list(FIPS.values())],
            ).fetchall()
        }
    tenants = {tenant: st for st, (tenant, _) in EV.items()}
    rows = []
    for link in sorted(raw._index(SOURCE)[1]):
        if link.startswith(HOST):
            st = link.removeprefix(HOST).split("/")[0]
            root = ElementTree.fromstring(zipfile.ZipFile(raw.cached(SOURCE, link).path).read("detail.xml"))
            if not re.fullmatch(r"\d+/\d+/(19|20)\d\d", root.findtext("ElectionDate") or ""):
                print(f"{link}: no election date, skipped")
                continue
            day = datetime.strptime(root.findtext("ElectionDate") or "", "%m/%d/%Y").date()
            title, official = root.findtext("ElectionName") or "", False
            items = [
                (
                    contest.get("text") or "",
                    [
                        (
                            choice.get("text") or "",
                            choice.get("party"),
                            place.get("name") or "",
                            int(place.get("votes") or 0),
                            [],
                        )
                        for choice in contest.iter("Choice")
                        for kind in choice.iter("VoteType")
                        for place in kind
                    ],
                )
                for contest in root.iter("Contest")
            ]
        elif link.startswith(API) and link.endswith("/data"):
            tenant, title = link.removeprefix(f"{API}elections/").split("/")[:2]
            st = tenants[tenant]
            data = json.loads(raw.cached(SOURCE, link).path.read_bytes())
            export = json.loads(raw.cached(SOURCE, f"{CDN}{data['jurisdiction']['mediaExportPath']}").path.read_bytes())
            day, official = date.fromisoformat(export["electionDate"][:10]), bool(data["election"]["isOfficialResults"])
            title = f"{title} {export['electionName'] or ''}"
            grouped: dict[str, list[tuple[str, str | None, str, int, list[tuple[str, int]]]]] = {}
            for locality in export["localResults"]:
                for item in locality["ballotItems"]:
                    grouped.setdefault(item["name"] or "", []).extend(
                        (
                            option["name"] or "",
                            option["politicalParty"],
                            locality["name"] or "",
                            int(option["voteCount"] or 0),
                            [
                                (place["name"] or "", int(place["voteCount"] or 0))
                                for place in option["precinctResults"] or []
                            ],
                        )
                        for option in item["ballotOptions"] or []
                    )
            items = list(grouped.items())
            del data, export
        else:
            continue
        heading = title.upper()
        if re.search(r"TEST|DEMO|MOCK|TRAINING", heading):
            continue
        primary = re.search(r"PRI|PPP", heading) is not None
        heading_party = re.search(r"REPUBLICAN|DEMOCRATIC|REP(?=_?PRI)|DEM(?=_?PRI)", heading)
        for text, options in items:
            text = re.sub(r"^(?:REP|DEM)\s*-\s*", lambda found: f"{found[0][:3]} ", " ".join(text.upper().split()))
            text = text.removeprefix("REP ").removeprefix("DEM ") + (
                f" - {text[:3]}" if text[:4] in ("REP ", "DEM ") else ""
            )
            office = next((name for name, pattern in OFFICES.items() if re.search(pattern, text)), None)
            if office is None or "DELEGATE" in text:
                continue
            if office == "statewide":
                found = re.search(OFFICES["statewide"], text)
                office = slug(re.sub(r"^(?:FOR )?(?:STATE )?", "", found[0] if found else ""))
            party = next((PARTIES[word] for word in re.findall(r"[A-Z]+", text) if word in PARTIES), None)
            if primary and party is None:
                party = PARTIES[heading_party[0][:3]] if heading_party else None
                tagged = {PARTIES.get((tag or "").upper()[:3]) for _, tag, _, _, _ in options if tag}
                party = party or (tagged.pop() if len(tagged) == 1 else None)
                if party is None:
                    print(f"{day} {st} {text}: primary with no party, skipped")
                    continue
            district = re.search(r"(?:DISTRICT|DIST)\D{0,3}(\d+)|(\d+)(?:ST|ND|RD|TH) (?:CONGRESSIONAL|DISTRICT)", text)
            for name, _, place, votes, precincts in options:
                label = re.sub(r"[^a-z]", "", place.lower().replace("&", "and"))
                code = counties.get((FIPS[st], label)) or counties.get((FIPS[st], f"{label}county"))
                if code is None:
                    raise ValueError(f"{link}: unmatched county {place}")
                county = re.sub(r"\s+COUNTY$", "", " ".join(place.upper().split()))
                base = {
                    "year": day.year,
                    "chamber": f"{office}:{party}" if party else office,
                    "day": day,
                    "special": "SPECIAL" in text,
                    "state": st,
                    "district": int(district[1] or district[2]) if office == "house" and district else None,
                    "fips": code,
                    "candidate": " ".join(name.split()).upper(),
                    "official": official,
                }
                rows.append({**base, "code": code, "kind": "county", "votes": votes})
                rows.extend(
                    {
                        **base,
                        "code": f"{day.year}:{st}:{code}:{county}:{' '.join(precinct.upper().split())}",
                        "kind": "precinct",
                        "votes": count,
                    }
                    for precinct, count in precincts
                )
    contest = ["day", "state", "chamber", "district", "special"]
    frame = (
        pl.DataFrame(rows, schema_overrides={"district": pl.Int16, "year": pl.Int32})
        .with_columns(
            measure=pl.col("candidate").replace_strict(medsl.PSEUDO, default=1, return_dtype=pl.Int16),
            writein=pl.col("candidate").is_in(list(medsl.POOLED)) | pl.col("candidate").str.contains("WRITE"),
        )
        .with_columns(candidate=pl.when("writein").then(pl.lit("WRITE-INS")).otherwise("candidate"))
        .filter(pl.col("measure") > 0)
        .group_by(
            "year", *contest[:3], "district", "special", "code", "fips", "kind", "candidate", "measure", "official"
        )
        .agg(pl.col("writein").any(), pl.col("votes").sum())
        .with_columns(source=pl.col("state"))
    )
    sums = (
        frame.group_by(*contest, "fips", "kind")
        .agg(pl.col("votes").sum())
        .pivot("kind", index=[*contest, "fips"], values="votes")
        .group_by(contest)
        .agg(
            precise=((pl.col("precinct") - pl.col("county")).abs() <= pl.max_horizontal(5, pl.col("county") * 0.005))
            .fill_null(False)
            .all()
        )
    )
    frame = (
        frame.join(sums, on=contest, how="left", nulls_equal=True)
        .filter(pl.when("precise").then(pl.col("kind") == "precinct").otherwise(pl.col("kind") == "county"))
        .drop("precise")
    )
    print(frame.group_by("state", "kind").agg(pl.struct(contest).n_unique().alias("contests")).sort("state", "kind"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "returns.parquet")


def emit() -> Path:
    returns = pl.read_parquet(PARSED / "returns.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Federal and statewide contests by county and precinct from the states' election night reporting sites",
            polity="US",
        )
        names = {**STATES, **{st: name for st, (_, name) in EV.items()}}
        sources = {}
        for st in sorted(set(returns["state"].to_list())):
            publisher = f"{names[st]} Secretary of State"
            sources[st] = cs.source(publisher, TITLE)
            if sources[st] in cs.minted:
                cs.add(
                    "source",
                    id=sources[st],
                    kind="official_body",
                    publisher=publisher,
                    title=TITLE,
                    url=f"{API.removesuffix('api/')}{EV[st][0]}" if st in EV else f"{HOST}{st}/",
                    license="Public record",
                )
        tenants = {tenant: st for st, (tenant, _) in EV.items()}
        for link in raw._index(SOURCE)[1]:
            st = (
                link.removeprefix(HOST).split("/")[0]
                if link.startswith(HOST)
                else tenants.get(link.removeprefix(CDN).removeprefix(f"{API}elections/").split("/")[0].lower())
            )
            if st in sources:
                cs.artifact(raw.cached(SOURCE, link), sources[st])
        filled = {
            (key, grain)
            for key, grain in conn.execute(
                """select distinct x.value, case when u.scheme_code = 'us.precinct' then 'precinct' else 'county' end
                   from external_id x
                   join result_set r on r.contest_id = x.entity_id and r.status = 'active'
                   join changeset g on g.id = r.changeset_id and g.pipeline <> %s
                   join result_value v on v.result_set_k = r.k
                   join reporting_unit u on u.k = v.unit_k
                     and u.scheme_code in ('us.precinct', 'us.county', 'us.county_part', 'us.town')
                   where x.scheme = 'us.contest' and split_part(x.value, ':', 2) = any(%s)""",
                [SOURCE, sorted(sources)],
            ).fetchall()
        }
        keyed = returns.with_columns(
            key=pl.when(pl.col("district").is_not_null())
            .then(
                pl.concat_str(
                    pl.col("day").cast(pl.String),
                    "state",
                    pl.lit("house"),
                    pl.col("district").cast(pl.String),
                    pl.col("chamber").str.strip_prefix("house").str.strip_prefix(":"),
                    separator=":",
                ).str.strip_suffix(":")
            )
            .otherwise(pl.concat_str(pl.col("day").cast(pl.String), "state", "chamber", separator=":"))
        )
        precincts = keyed.filter(
            (pl.col("kind") == "precinct") & ~pl.col("key").is_in([key for key, grain in filled if grain == "precinct"])
        )
        counties = keyed.filter((pl.col("kind") == "county") & ~pl.col("key").is_in([key for key, _ in filled]))
        medsl.attach(cs, conn, counties.drop("key"), sources, strict=True)
        medsl.attach(cs, conn, precincts.drop("key"), sources, grain="precinct", strict=True)
        unofficial = {
            contest
            for (contest,) in conn.execute(
                "select entity_id from external_id where scheme = 'us.contest' and value = any(%s)",
                [keyed.filter(~pl.col("official"))["key"].unique().to_list()],
            ).fetchall()
        }
        for row in cs.rows.get("result_set", []):
            if row["status"] == "active" and row["contest_id"] in unofficial:
                row["stage"] = "preliminary"
                cs.add("note", entity_id=row["id"], kind="caveat", body_md=CAVEAT)
        used = set(pl.concat(cs.frames["result_value"])["unit_id"].unique()) if cs.frames.get("result_value") else set()
        parents = {
            code: (unit, area)
            for code, unit, area in conn.execute(
                "select code, id, area_id from reporting_unit where scheme_code = 'us.county' and left(code, 2) = any(%s)",
                [[FIPS[st] for st in sources]],
            ).fetchall()
        }
        units = (
            precincts.group_by("code")
            .agg(pl.col("fips").first(), pl.col("day").min(), pl.col("state").first())
            .with_columns(
                id=pl.col("code").map_elements(lambda code: str(cs.unit("us.precinct", code)), return_dtype=pl.String)
            )
            .filter(pl.col("id").is_in(list(used)) & pl.col("id").is_in([str(unit) for unit in cs.minted]))
        )
        cs.frame(
            "reporting_unit",
            units.select(
                "id",
                polity_id=pl.col("state").map_elements(lambda st: str(cs.polity(f"US-{st}")), return_dtype=pl.String),
                scheme_code=pl.lit("us.precinct"),
                code="code",
                parent_id=pl.col("fips").replace_strict({code: str(unit) for code, (unit, _) in parents.items()}),
                area_relation=pl.lit("within"),
                area_id=pl.col("fips").replace_strict({code: str(area) for code, (_, area) in parents.items()}),
                kind=pl.lit("standard"),
                valid_from="day",
                valid_to=pl.col("day") + timedelta(days=1),
            ),
        )
        print(f"{units.height} new precinct units")
        return cs.write()
