import io
import re
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from datetime import date, datetime
from pathlib import Path

import polars as pl

from ea import raw
from ea.changeset import Changeset
from ea.sources.us import openelections
from ea.store import DATA, connect

SOURCE = "us.ga_turnout"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
PUBLISHER = "Georgia Secretary of State"
DEMOGRAPHICS = "https://web.archive.org/web/20241106020656id_/https://sos.ga.gov/sites/default/files/bulk/Voter_Turn_Out_By_Demographics.zip"
URL = "https://web.archive.org/web/2026id_/https://sos.ga.gov/sites/default/files/2026-04/{}.zip"
ZIPS = {
    "july_31_2012_-_general_primary-general_nonpartisan-special_election": "primary",
    "august_21_2012_-_general_primary_runoff": "runoff",
    "may_20_2014_-_gen_primary_np_general": "primary",
    "july_22_2014_-_gen_primary_np_general_runoff": "runoff",
    "march_1_2016_-_presidential_preference_primary": "primary",
    "may_24_2016_-_general_primary": "primary",
    "july_26_2016_-_general_primary_runoff": "runoff",
    "june_20_2017_-_special_runoff": "runoff",
    "may_22_2018_-_general_primary_election": "primary",
    "july_24_2018_-_general_primary_runoff_election": "runoff",
    "december_4_2018_-_general_election_runoff": "runoff",
    "june_9_2020_-_presidential_preference_primary_-_general_primary_-_nonpartisan_general_election_-_special_election": "primary",
    "august_11_2020_-_general_primary_-_nonpartisan_general_election_runoff": "runoff",
    "december_1_2020_-_state_runoff": "runoff",
    "january_5_2021_-_federal_runoff": "runoff",
    "may_24_2022_-_general_primary_-nonpartisan_general_election": "primary",
    "june_21_2022_-_general_primary_-nonpartisan_general_election_runoff": "runoff",
    "december_6_2022_-_general_election_runoff": "runoff",
    "january_31_2023_-_special_election": "runoff",
    "february_28_2023_-_ga_house_district_119_-_runoff_election": "runoff",
    "june_13_2023_-_house_district_68_runoff_election": "runoff",
    "march_12_2024_-_house_district_125_runoff": "runoff",
    "march_12_2024_-_presidential_preference_primary": "primary",
    "may_7_2024_-_house_district_139_special_election_runoff": "runoff",
    "may_21_2024_-_general_primary_-_nonpartisan_election": "primary",
    "june_18_2024_-_general_primary_-_nonpartisan_general_election_runoff": "runoff",
}
PARTY = re.compile(r"\b(DEM|REP|LIB|GRN)\b")
DATE = re.compile(r"ELECTION DATE:\s*([A-Z]+ \d+, \d{4})")
SECTION = re.compile(r"^\s*([A-Z ]+?) VOTING HISTORY( SUMMARY)?\s*$")
WELL_FORMED = re.compile(r"\d{1,3}(,\d{3})*")
ROW = re.compile(r"^\s*[A-Z][A-Z .'-]*?\s+(\d{3})((?:\s+\d[\d,]*)+)\s*$")


def fetch() -> None:
    raw.fetch(SOURCE, DEMOGRAPHICS, suffix=".zip")
    for name in ZIPS:
        raw.fetch(SOURCE, URL.format(name), suffix=".zip")


def balanced(values: list[int], combined: bool) -> bool:
    if not combined:
        return values[-2] == sum(values[0:-2:2]) and values[-1] == sum(values[1:-2:2])
    dem = values[:16]
    return balanced(dem, False) and values[-2] == values[-4] == dem[-2] and values[-1] == dem[-1] + values[-3]


def repair(tokens: list[str], width: int, combined: bool) -> list[int] | None:
    candidates = [tokens]
    for _ in range(0 if combined else width - len(tokens)):
        candidates = [
            c[:i] + [c[i][:j], c[i][j:]] + c[i + 1 :]
            for c in candidates
            for i in range(len(c))
            for j in range(1, len(c[i]))
            if WELL_FORMED.fullmatch(c[i][:j]) and WELL_FORMED.fullmatch(c[i][j:])
        ]
    fits = {tuple(int(t.replace(",", "")) for t in c) for c in candidates if combined or len(c) == width}
    fits = {f for f in fits if balanced(list(f), combined)}
    return list(fits.pop()) if len(fits) == 1 else None


def demographics() -> list[tuple[date, str, int, str, int, int]]:
    rows = []
    with zipfile.ZipFile(raw.cached(SOURCE, DEMOGRAPHICS).path) as outer:
        for member in sorted(outer.namelist()):
            if member.startswith("__MACOSX") or not re.search(r"_County( \((Dem|Rep)\))?\.(pdf|zip|xlsx)$|and County\.pdf$", member):
                continue
            content = outer.read(member)
            if member.endswith(".pdf"):
                with tempfile.NamedTemporaryFile(suffix=".pdf") as handle:
                    handle.write(content)
                    handle.flush()
                    text = subprocess.run(["pdftotext", "-layout", handle.name, "-"], capture_output=True, text=True, check=True).stdout
                found = DATE.search(text)
                if found is None:
                    print(f"{member}: no text layer")
                    continue
                day = datetime.strptime(found[1], "%B %d, %Y").date()
                section, totals, found_rows = "", {}, []
                for line in text.splitlines():
                    if heading := SECTION.match(line):
                        section = heading[1] + (" SUMMARY" if heading[2] else "")
                        continue
                    numbers = [int(n.replace(",", "")) for n in re.findall(r"\d[\d,]*", line)]
                    if section.endswith("SUMMARY") and len(numbers) >= 27 and "%" not in line:
                        top = numbers.index(max(numbers))
                        voted = numbers[-1] if numbers.count(numbers[top]) == 3 else sum(numbers[1:top:2])
                        totals[section.removesuffix(" SUMMARY")] = (numbers[top], voted)
                    elif not section.endswith("SUMMARY") and (match := ROW.match(line)):
                        found_rows.append((section, int(match[1]), match[2].split()))
                width = {
                    section: max(set(counts), key=counts.count)
                    for section in {r[0] for r in found_rows}
                    for counts in [[len(r[2]) for r in found_rows if r[0] == section]]
                }
                sums = {}
                for section, county, tokens in found_rows:
                    values = repair(tokens, width[section], "AND" in section)
                    if values is None:
                        print(f"{day} {section}: county {county} fails its sums: {' '.join(tokens)[:120]}")
                        continue
                    rows.append((day, section, county, "", values[-2], values[-1]))
                    registered, voted = sums.get(section, (0, 0))
                    sums[section] = (registered + values[-2], voted + values[-1])
                for section, total in totals.items():
                    if sums.get(section) != total:
                        print(f"{day} {section}: counties sum to {sums.get(section)}, summary prints {total}")
                continue
            books = []
            if member.endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(content)) as inner:
                    books = [inner.read(n) for n in inner.namelist() if n.endswith(".xlsx")]
            else:
                books = [content]
            for book in books:
                frame = pl.read_excel(io.BytesIO(book), has_header=False, infer_schema_length=0)
                title = str(frame.item(0, 0))
                day = datetime.strptime(DATE.search(title)[1], "%B %d, %Y").date()
                party = re.search(r"PARTY: (\w+)", title)
                section = party[1] if party else re.search(r"\n(.+?) VOTING HISTORY", title)[1]
                for values in frame.rows():
                    cells = [v for v in values if v not in (None, "")]
                    if len(cells) > 4 and re.fullmatch(r"\d{3}", str(cells[1])):
                        rows.append((day, section, int(cells[1]), str(cells[0]), int(cells[-3]), int(cells[-2])))
    return rows


def clarity() -> list[tuple[date, str, str, int, int]]:
    rows, bad = [], set()
    for name in ZIPS:
        with zipfile.ZipFile(raw.cached(SOURCE, URL.format(name)).path) as outer:
            info = next(n for n in outer.namelist() if n.endswith("info.txt"))
            month, day_of, year = re.search(r"Election Date: (\d+)/(\d+)/(\d+)", outer.read(info).decode(errors="replace")).groups()
            day = date(int(year), int(month), int(day_of))
            found, failed = [], []
            for member in sorted(n for n in outer.namelist() if n.endswith("-detailxml.zip")):
                with zipfile.ZipFile(io.BytesIO(outer.read(member))) as inner:
                    root = ET.fromstring(inner.read("detail.xml"))
                turnout = root.find("VoterTurnout")
                if turnout is None or turnout.get("ballotsCast") is None or turnout.get("totalVoters") is None:
                    continue
                registered, ballots = int(turnout.get("totalVoters")), int(turnout.get("ballotsCast"))
                if registered == 0 and ballots == 0:
                    continue
                best: dict[str, int] = {}
                for contest in root.findall("Contest"):
                    party = PARTY.search(contest.get("text"))
                    total = sum(int(v.get("votes")) for choice in contest.findall("Choice") for v in choice.findall("VoteType"))
                    key = party[1] if party else ""
                    best[key] = max(best.get(key, 0), total)
                floor = max(sum(v for k, v in best.items() if k), max(best.values(), default=0))
                county = root.findtext("Region")
                if not floor <= ballots <= registered:
                    failed.append(county)
                found.append((day, URL.format(name), county, registered, ballots))
            print(day, "clarity", len(found), "counties", f"{len(failed)} fail the floor check" if failed else "")
            rows += found
            if failed:
                bad.add(day)
    return [row for row in rows if row[0] not in bad]


def parse() -> None:
    with connect() as conn:
        fips = openelections.counties(conn)["GA"]
    frame = pl.DataFrame(demographics(), schema=["day", "section", "county", "name", "registered", "voters"], orient="row")
    names = dict(frame.filter(pl.col("name") != "").select("county", "name").unique().iter_rows())
    codes = {county: fips[re.sub(r"[^a-z]", "", name.lower())] for county, name in names.items()}
    assert len(codes) == len(set(codes.values())) == 159
    frame = frame.filter(pl.col("day") >= date(2004, 1, 1), pl.col("county").max().over("day") <= 159)
    mismatched = frame.group_by("day", "county").agg(pl.col("registered").n_unique().alias("n")).filter(pl.col("n") > 1)
    if mismatched.height:
        print("registered differs between sections:", mismatched.group_by("day").len().sort("day").rows())
    frame = (
        frame.group_by("day", "county")
        .agg(pl.col("registered").max(), pl.col("voters").sum())
        .with_columns(
            code=pl.col("county").replace_strict(codes), measure=pl.lit(14), url=pl.lit(DEMOGRAPHICS)
        )
        .drop("county")
    )
    held = set(frame["day"])
    extra = [
        (day, url, fips[re.sub(r"[^a-z]", "", county.lower())], registered, ballots)
        for day, url, county, registered, ballots in clarity()
        if day not in held
    ]
    if extra:
        frame = pl.concat(
            [
                frame,
                pl.DataFrame(extra, schema=["day", "url", "code", "registered", "voters"], orient="row").with_columns(measure=pl.lit(3)),
            ],
            how="diagonal_relaxed",
        )
    print(frame.group_by("day", "measure").agg(pl.len(), pl.col("registered").sum(), pl.col("voters").sum()).sort("day"))
    PARSED.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(PARSED / "turnout.parquet")


def emit() -> Path:
    frame = pl.read_parquet(PARSED / "turnout.parquet")
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary="Georgia's registered voters and voters by county from the Secretary of State's voter turnout by demographics reports (active, or active and inactive from 2016, registrants; voters with credit for the election, both parties' voters summed in primaries), plus ballots cast from the Clarity results files for days those reports skip",
            polity="US",
        )
        elections: dict[date, list[tuple[str, str]]] = {}
        for value, entity_id, start in conn.execute(
            """select x.value, x.entity_id, e.date_from from external_id x join election e on e.id = x.entity_id
               where x.scheme = 'us.election' and x.value ~ ':GA(:[a-z]+)?$'"""
        ):
            elections.setdefault(start, []).append((value, entity_id))
        held = {
            election_id
            for (election_id,) in conn.execute(
                """select r.election_id from result_set r join changeset c on c.id = r.changeset_id
                   where r.contest_id is null and r.is_default and r.status <> 'withdrawn' and c.pipeline <> %s""",
                [SOURCE],
            )
        }
        sources = {}
        for url, title, home in (
            (DEMOGRAPHICS, "Voter Turn Out By Demographics", "https://sos.ga.gov/page/election-data-hub-turnout"),
            (None, "Historical Elections Results", "https://sos.ga.gov/page/historical-elections-results"),
        ):
            source = cs.source(PUBLISHER, title)
            if source in cs.minted:
                cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=title, url=home, license="Public record")
            sources[url] = source
        for (day, url, measure), rows in frame.group_by("day", "url", "measure", maintain_order=True):
            if day not in elections:
                print(f"{day}: no election")
                continue
            source = sources.get(url, sources[None])
            cs.artifact(raw.cached(SOURCE, url), source)
            for value_key, election_id in elections[day]:
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
                    completeness=round(rows.height / 159, 4),
                )
                for code, registered, voters in rows.select("code", "registered", "voters").iter_rows():
                    for m, value in ((2, registered), (measure, voters)):
                        cs.add(
                            "result_value",
                            result_set_id=result_set,
                            unit_id=cs.unit("us.county", code),
                            option_id=None,
                            channel=0,
                            measure=m,
                            count_round=0,
                            flags=0,
                            value=value,
                        )
        return cs.write()
