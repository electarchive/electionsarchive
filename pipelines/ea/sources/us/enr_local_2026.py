import csv
import io
import json
import os
import re
import zipfile
from collections import Counter
from datetime import UTC, date, datetime
from uuid import UUID

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.sources.us.enr_2026 import HEADERS, NOTE, STOP, SUFFIXES, YESNO, get, person
from ea.sources.us.la_judicial_2026 import classify as la_classify
from ea.sources.us.oe_primaries_2026 import ALIASES, FIPS, OFFICIAL, plain
from ea.store import DATA, connect

SOURCE = "us.enr_local_2026"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
DAY = date.fromisoformat(os.environ.get("ENR_LOCAL_2026_DAY", "2026-11-03"))
FEED_DAY = date.fromisoformat(os.environ.get("ENR_LOCAL_2026_FEED", DAY.isoformat()))
ONLY = set(filter(None, os.environ.get("ENR_LOCAL_2026_STATES", "").split(",")))
MN_BASE = "https://electionresultsfiles.sos.mn.gov/{:%Y%m%d}/"
LA_BASE = "https://voterportal.sos.la.gov/ElectionResults/ElectionResults/Data?blob={:%Y%m%d}/"
LA_NOT_LOCAL = re.compile(r"U\. ?S\.|PSC|BESE|State Representative|CA No")
LA_TOWN = re.compile(
    r"(Mayor|Alderm[a-z]+(?: at Large)?|Councilman|Chief of Police) -- (?:(.+?), )?(?:City|Town|Village) of (.+)"
)
LA_MEASURES = {"City of Shreveport (Downtown Dev. Dist.)": "LA:2270000:measure:city-of-shreveport-proposition"}
LA_HELD = {"Mayor": "mayor", "Alderm": "board-of-aldermen", "Councilman": "council", "Chief": "chief-of-police"}
MN_FILES = ("local.txt", "allracesbyprecinct.txt")
NC_URL = "https://s3.amazonaws.com/dl.ncsbe.gov/ENRS/{:%Y_%m_%d}/results_pct_{:%Y%m%d}.zip"
MN_HELD = (
    ("mayor", r"MAYOR\b"),
    ("council", r"COUNCIL MEMBER\b"),
    ("town-board", r"TOWN SUPERVISOR\b"),
    ("clerk-treasurer", r"(?:TOWN|CITY) CLERK ?- ?TREASURER\b"),
    ("clerk", r"(?:TOWN|CITY) CLERK\b"),
    ("treasurer", r"(?:TOWN|CITY) TREASURER\b"),
)
MN_COUNTY_OFFICES = (
    (r"COUNTY SHERIFF", "sheriff"),
    (r"COUNTY ATTORNEY", "county-attorney"),
    (r"COUNTY AUDITOR ?/ ?TREASURER", "auditor-treasurer"),
    (r"COUNTY AUDITOR", "auditor"),
    (r"COUNTY TREASURER", "treasurer"),
    (r"COUNTY RECORDER", "recorder"),
    (r"COUNTY SURVEYOR", "surveyor"),
    (r"COUNTY COMMISSIONER DISTRICT (\d+)", "commission:district-{}"),
    (r"SOIL AND WATER (?:SUPERVISOR|DISTRICT SUPERVISOR) DISTRICT (\d+)", "soil-and-water:district-{}"),
)
MN_NOT_LOCAL = re.compile(
    r"U\.S\.|GOVERNOR|STATE AUDITOR|SECRETARY OF STATE|ATTORNEY GENERAL|STATE SENATOR|STATE REPRESENTATIVE|JUDGE|JUSTICE",
    re.IGNORECASE,
)
NC_NOT_LOCAL = re.compile(r"US SENATE|NC GOVERNOR|US HOUSE|NC STATE|NC HOUSE|NC SENATE|NC SUPREME|NC COURT OF APPEALS")
ROMAN = {"I": 1, "II": 2, "III": 3, "IV": 4, "V": 5}
NC_OFFICE = re.compile(
    r"BOARD OF COMMISSIONERS|BOARD OF EDUCATION|SHERIFF|CLERK OF SUPERIOR COURT|REGISTER OF DEEDS|SOIL AND WATER[A-Z ]*?(?= DISTRICT|$)"
)
NC_TOWN = re.compile(r"(?:CITY|TOWN|VILLAGE) OF (.+?) (MAYOR|COUNCIL(?: MEMBER)?(?: [A-Z ]+)?|COMMISSIONER[A-Z ]*)")
NC_PARTY = re.compile(r"\s*\((DEM|REP|LIB|UNA|GRE|CST)\)$")


def fetch() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    pages: dict[str, dict[str, str]] = {}
    client = httpx.Client(follow_redirects=True, timeout=300, headers=HEADERS)

    def store(state: str, url: str, content: bytes, suffix: str) -> None:
        key = f"{url}@{stamp}"
        raw.store(
            SOURCE,
            key,
            content,
            suffix=suffix,
            retrieved_at=datetime.now(UTC),
            media_type="text/tab-separated-values",
        )
        pages.setdefault(state, {})[url] = key

    if not ONLY or "MN" in ONLY:
        for name in MN_FILES:
            url = MN_BASE.format(FEED_DAY) + name
            content = get(client, url)
            if content is None:
                print(f"MN: no file {name}")
            else:
                store("MN", url, content, ".txt")
    if not ONLY or "LA" in ONLY:
        listing_url = LA_BASE.format(FEED_DAY) + "RacesCandidates_Multiparish.htm"
        listing = get(client, listing_url)
        if listing is None:
            print("LA: no race listing")
        else:
            store("LA", listing_url, listing, ".json")
            for race in json.loads(listing)["Races"]["Race"]:
                url = LA_BASE.format(FEED_DAY) + f"csv/ByParish_{race['ID']}.csv"
                content = get(client, url)
                if content is None:
                    print(f"LA: no file for race {race['ID']}")
                else:
                    store("LA", url, content, ".csv")
    if not ONLY or "NC" in ONLY:
        url = NC_URL.format(FEED_DAY, FEED_DAY)
        content = get(client, url)
        if content is None:
            print("NC: no zip")
        else:
            store("NC", url, content, ".zip")
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "pages.json").write_text(json.dumps(pages))
    print({state: len(urls) for state, urls in pages.items()})


def rows(state: str, url: str, path):
    if state == "MN":
        county = url.endswith("allracesbyprecinct.txt")
        totals: dict[tuple[str, str, str, str], list[int]] = {}
        for line in path.read_text(encoding="latin-1").splitlines():
            cells = [cell.strip().strip('"').strip() for cell in line.split(";")]
            if len(cells) < 14 or MN_NOT_LOCAL.match(cells[4]):
                continue
            title = re.sub(r"\s+", " ", cells[4])
            geo = f"27{2 * int(cells[1]) - 1:03}" if county else cells[5]
            candidate = re.sub(r"\s+", " ", cells[7].split(" and ")[0])
            tally = totals.setdefault((title, geo, cells[3], candidate), [0, 0, 0])
            tally[0] += int(cells[13] or 0)
            if not county:
                tally[1], tally[2] = int(cells[11] or 0), int(cells[12] or 0)
        for (title, geo, _, candidate), (votes, reporting, total) in totals.items():
            yield {
                "contest": title,
                "geo": geo,
                "county": geo if geo.startswith("27") and len(geo) == 5 else "",
                "candidate": candidate,
                "write_in": candidate.upper().startswith("WRITE-IN"),
                "votes": votes,
                "reporting": reporting or None,
                "total": total or None,
            }
        return
    if state == "LA":
        if url.endswith(".htm"):
            return
        table = csv.reader(path.read_text(encoding="utf-8-sig").splitlines())
        header = next(table)
        for row in table:
            if LA_NOT_LOCAL.match(row[0].strip()):
                continue
            for label, votes in zip(header[2:], row[2:], strict=False):
                named = re.fullmatch(r"(.*?)\s*\((\w+)\)", label.strip())
                candidate = named[1] if named else label.strip()
                yield {
                    "contest": re.sub(r"\s+", " ", row[0]).strip(),
                    "geo": "",
                    "county": row[1],
                    "candidate": candidate,
                    "write_in": "write-in" in label.lower(),
                    "votes": int(votes or 0),
                    "reporting": None,
                    "total": None,
                }
        return
    if state == "NC":
        totals: dict[tuple[str, str, str], int] = {}
        with zipfile.ZipFile(path) as archive, archive.open(archive.namelist()[0]) as handle:
            for row in csv.DictReader(io.TextIOWrapper(handle, encoding="utf-8"), delimiter="\t"):
                name = NC_PARTY.sub("", row["Contest Name"].strip())
                if NC_NOT_LOCAL.match(name):
                    continue
                key = (name, row["County"].title(), row["Choice"].strip())
                totals[key] = totals.get(key, 0) + int(row["Total Votes"] or 0)
        for (name, county, choice), votes in totals.items():
            yield {
                "contest": name,
                "geo": "",
                "county": county,
                "candidate": choice,
                "write_in": choice.lower().startswith("write-in"),
                "votes": votes,
                "reporting": None,
                "total": None,
            }


def parse() -> None:
    pages = json.loads((PARSED / "pages.json").read_text())
    table = []
    for state, urls in pages.items():
        for url, key in urls.items():
            for row in rows(state, url, raw.cached(SOURCE, key).path):
                table.append({"state": state, "page": url, **row})
    frame = pl.DataFrame(
        table,
        schema={
            "state": pl.String,
            "page": pl.String,
            "contest": pl.String,
            "geo": pl.String,
            "county": pl.String,
            "candidate": pl.String,
            "write_in": pl.Boolean,
            "votes": pl.Int64,
            "reporting": pl.Int64,
            "total": pl.Int64,
        },
    )
    frame.write_parquet(PARSED / "results.parquet")
    print(frame.group_by("state").agg(pl.len(), pl.col("votes").sum()).sort("state"))


def seat_tail(title: str) -> str:
    found = re.search(r"(?i)\b(ward \d+|district \d+|position \d+|seat [a-z0-9]+|at large)\b", title) or re.search(
        r"(?i)\b((?!member\b)[a-z]+ district)$", title
    )
    return (
        ":" + slug(re.sub(r"(?i)^at large$", "At-Large", found[1].title().replace("At Large", "At-Large")))
        if found
        else ""
    )


def derive(
    state: str, name: str, geo: str, codes: dict[str, list[str]], counties: dict[str, str], places: dict[str, list[str]]
) -> list[str]:
    if state == "MN":
        upper = name.upper()
        if re.fullmatch(r"27\d{3}", geo):
            for pattern, tail in MN_COUNTY_OFFICES:
                found = re.fullmatch(rf"(?:SPECIAL ELECTION FOR )?{pattern}", upper)
                if found:
                    special = ":special" if upper.startswith("SPECIAL") else ""
                    return [f"MN:{geo}:{tail.format(*found.groups())}{special}"]
            if "QUESTION" in upper:
                return [f"MN:{geo}:question:{slug(name)}"]
            return []
        stem = re.sub(r"\s*\([^)]*\)", "", name).strip()
        special = ":special" if upper.startswith("SPECIAL ELECTION") else ""
        if "QUESTION" in upper:
            return [f"MN:{code}:question:{slug(stem)}" for code in codes.get(geo, [])]
        held = next(
            (held for held, pattern in MN_HELD if re.match(rf"(?:SPECIAL ELECTION FOR )?{pattern}", upper)), None
        )
        return [f"MN:{code}:{held}{seat_tail(stem)}{special}" for code in codes.get(geo, [])] if held else []
    if state == "LA":
        found = la_classify(name)
        if found:
            return [found[1]]
        for prefix, tail in LA_MEASURES.items():
            if name.startswith(prefix):
                return [tail]
        town = LA_TOWN.fullmatch(name)
        if town and plain(town[3]) in places:
            held = LA_HELD[next(key for key in LA_HELD if town[1].startswith(key))]
            seat = ":at-large" if "at Large" in town[1] else seat_tail(town[2] or "")
            return [f"LA:{code}:{held}{seat}" for code in places[plain(town[3])]] + [
                f"LA:{code}:{held}" for code in places[plain(town[3])]
            ]
        return []
    if state == "NC":
        unexpired = "(UNEXPIRED)" in name
        name = re.sub(r"\s*\(UNEXPIRED\)", "", name)
        county = re.match(r"(.+?) COUNTY (?:SCHOOLS )?(.+)$", name)
        if county and county[1].lower() in counties:
            fips = counties[county[1].lower()]
            rest = re.sub(r"\b(I{1,3}|IV|V)$", lambda found: str(ROMAN[found[1]]), county[2])
            office = NC_OFFICE.match(rest)
            if office is None:
                return [f"NC:{fips}:measure:{slug(rest)}"]
            suffix = rest[office.end() :].strip()
            base = slug(office[0])
            district = re.fullmatch(r"DISTRICT (\d+)", suffix)
            township = re.fullmatch(r"(.+?) TWP", suffix)
            seat = (
                f":district-{int(district[1])}"
                if district
                else ":at-large"
                if suffix.startswith("AT-LARGE")
                else f":{slug(township[1])}-township"
                if township
                else f":{slug(suffix)}"
                if suffix and not re.fullmatch(r"CHAIRMAN|MEMBERS", suffix)
                else ""
            )
            end = ":unexpired" if unexpired else ""
            return [f"NC:{fips}:{base}{seat}{end}", f"NC:{fips}:{base}{seat}", f"NC:{fips}:{base}"]
        town = NC_TOWN.fullmatch(name)
        if town and plain(town[1]) in places:
            held = "mayor" if town[2] == "MAYOR" else "council" if town[2].startswith("COUNCIL") else slug(town[2])
            return [f"NC:{code}:{held}" for code in places[plain(town[1])]] + [
                f"NC:{code}:{held}{seat_tail(town[2])}" for code in places[plain(town[1])]
            ]
        measure = re.fullmatch(r"(?:CITY|TOWN|VILLAGE) OF (.+?) ([A-Z ]*(?:REFERENDUM|ELECTION|BONDS?).*)", name)
        if measure and plain(measure[1]) in places:
            return [f"NC:{code}:measure:{slug(measure[2])}" for code in places[plain(measure[1])]]
        da = re.fullmatch(r"DISTRICT ATTORNEY DISTRICT (\w+)", name)
        if da:
            return [f"NC:da:{da[1].lstrip('0').lower()}"]
        court = re.fullmatch(r"NC DISTRICT COURT JUDGE DISTRICT (\w+?)(?: SEAT (\d+))?( \(UNEXPIRED\))?", name)
        if court:
            seat = f":seat-{court[2]}" if court[2] else ""
            return [f"NC:district-court:{court[1].lower()}{seat}" + (":unexpired" if court[3] else "")]
    return []


def emit():
    frame = pl.read_parquet(PARSED / "results.parquet")
    pages = json.loads((PARSED / "pages.json").read_text())
    stats: Counter[str] = Counter()
    misses: Counter[str] = Counter()
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"Unofficial election night results of {DAY} for local contests, from the states' reporting sites",
            polity="US",
        )
        counties: dict[tuple[str, str], UUID] = {}
        codes: dict[tuple[str, str], str] = {}
        names: dict[str, dict[str, str]] = {}
        for code, text, id in conn.execute(
            """select u.code, n.text, u.id from reporting_unit u
               join name n on n.entity_id = u.area_id and n.kind = 'short'
               where u.scheme_code = 'us.county' and u.code ~ %s order by u.code""",
            [f"^({'|'.join(FIPS.values())})"],
        ).fetchall():
            counties.setdefault((code[:2], plain(text)), id)
            counties[(code[:2], code)] = id
            codes.setdefault((code[:2], plain(text)), code)
            names.setdefault(code[:2], {}).setdefault(text.lower(), code)
        sources: dict[str, UUID] = {}
        work: list[tuple[str, str, UUID, pl.DataFrame, UUID | None]] = []
        taken: set[str] = set()
        for state in frame["state"].unique(maintain_order=True):
            part = frame.filter(pl.col("state") == state)
            published: dict[UUID, tuple[str, set[str]]] = {}
            for key, id in conn.execute(
                "select x.value, x.entity_id from external_id x where x.scheme = 'us.contest' and x.value like %s",
                [f"{DAY}:{state}:%"],
            ).fetchall():
                if not re.fullmatch(rf"{DAY}:{state}:(house:\d+|senate|governor)", key):
                    published[id] = (key, set())
            for contest, label in conn.execute(
                """select o.contest_id, coalesce(ca.ballot_name, o.ballot_label, '') from contest_option o
                   left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where o.parent_option_id is null and o.kind <> 'write_in' and o.contest_id = any(%s)""",
                [list(published)],
            ).fetchall():
                published[contest][1].add(person(label))
            by_key = {key: (id, wanted) for id, (key, wanted) in published.items()}
            titles: dict[UUID, set[str]] = {}
            for contest, text in conn.execute(
                "select entity_id, text from name where entity_id = any(%s)", [list(published)]
            ).fetchall():
                titles.setdefault(contest, set()).update(slug(text).split("-"))
            towns: dict[str, set[str]] = {}
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id
                   where u.scheme_code = 'us.place' and u.code = any(%s)""",
                [list({key.split(":")[2] for key, _ in published.values()})],
            ).fetchall():
                towns.setdefault(code, set()).update(w for w in slug(text).split("-") if len(w) > 2 and w not in STOP)
            mn_codes: dict[str, list[str]] = {}
            if state == "MN":
                for (tail,) in conn.execute(
                    """select distinct regexp_replace(value, '^[0-9-]+:MN:', '') from external_id
                       where scheme = 'us.contest' and value ~ '^[0-9-]+:MN:27[0-9]{5}'"""
                ).fetchall():
                    geoid = tail.split(":")[0]
                    if geoid not in mn_codes.setdefault(geoid[-5:], []):
                        mn_codes[geoid[-5:]].append(geoid)
            place_names: dict[str, list[str]] = {}
            for code, text in conn.execute(
                """select u.code, n.text from reporting_unit u join name n on n.entity_id = u.area_id and n.kind = 'short'
                   where u.scheme_code = 'us.place' and u.code like %s""",
                [f"{FIPS[state]}%"],
            ).fetchall():
                place_names.setdefault(plain(text), []).append(code)
            races: dict[tuple[str, str], tuple[frozenset[str], list[str]]] = {}
            for (name, geo, county), group in part.group_by("contest", "geo", "county"):
                people = frozenset(
                    person(candidate) for candidate in group.filter(~pl.col("write_in"))["candidate"].unique()
                )
                known, places = races.get((name, geo), (frozenset(), []))
                races[(name, geo)] = (known | people, places + ([county] if county else []))
            matched: list[tuple[str, str, str, list[str], str]] = []
            for (name, geo), (people, places) in races.items():
                stats[f"{state} feed races"] += 1
                tails = derive(state, name, geo, mn_codes, names.get(FIPS[state], {}), place_names)
                yesno = people <= {"yes", "no"} and bool(people)
                reason = "no derivation" if not tails else "derived key not published"
                hit = None
                for tail in tails:
                    key = f"{DAY}:{tail}"
                    if key not in by_key:
                        continue
                    wanted = by_key[key][1]
                    overlap = len(people & wanted) / len(people | wanted) if people | wanted else 0
                    if len(people) >= 2 and not yesno:
                        ok = overlap >= 0.5
                    else:
                        ok = yesno or bool(people & wanted)
                    if ok:
                        hit = (key, "derived")
                        break
                    reason = "derived key candidates disagree"
                if hit is None and len(people) >= 2 and not yesno:
                    hit = fallback(state, name, people, places, published, titles, towns, codes)
                    if hit is not None:
                        hit = (hit, "fallback")
                if hit is None:
                    misses[f"{state}: {reason}"] += 1
                    print(f"miss {state}: {name} ({geo or ', '.join(places[:2])}): {reason}")
                    continue
                if hit[0] in taken:
                    misses[f"{state}: duplicate key"] += 1
                    print(f"miss {state}: {name}: duplicate of {hit[0]}")
                    continue
                taken.add(hit[0])
                stats[f"{state} matched {hit[1]}"] += 1
                matched.append((name, geo, hit[0], places, hit[1]))
            for name, geo, key, places, how in matched:
                contest = by_key[key][0]
                segment = key.split(":")[2]
                place = None
                if not re.fullmatch(r"\d{5}", segment) and re.match(r"\d|sd", segment):
                    found = conn.execute(
                        """select u.id from reporting_unit u
                           where u.area_id = (select district_area_id from contest where id = %s)
                              or u.scheme_code = 'us.place' and u.code = %s
                           order by u.scheme_code = 'us.place' and u.code = %s, u.scheme_code limit 1""",
                        [contest, segment, segment],
                    ).fetchone()
                    if found is None:
                        misses[f"{state}: no unit for key"] += 1
                        print(f"miss {state}: {name}: no unit for {key}")
                        continue
                    place = found[0]
                group = part.filter((pl.col("contest") == name) & (pl.col("geo") == geo))
                work.append((state, key, contest, group, place))
        stats["matched total"] = len(work)
        for state, contest_key, contest, group, place in work:
            election = conn.execute("select election_id from contest where id = %s", [contest]).fetchone()[0]
            if state not in sources:
                publisher, site, _ = OFFICIAL.get(
                    state,
                    ("Minnesota Secretary of State", "https://www.sos.mn.gov/elections-voting/election-results/", ""),
                )
                title = f"Unofficial Election Night Results {DAY}: {state} local"
                sources[state] = cs.source(publisher, title)
                if sources[state] in cs.minted:
                    cs.add(
                        "source",
                        id=sources[state],
                        kind="official_body",
                        publisher=publisher,
                        title=title,
                        url=site,
                        license="Public record",
                    )
                for key in pages[state].values():
                    cs.artifact(raw.cached(SOURCE, key), sources[state])
            options = conn.execute(
                """select o.id, o.kind, coalesce(ca.ballot_name, o.ballot_label, '') from contest_option o
                   left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where o.contest_id = %s and o.parent_option_id is null""",
                [contest],
            ).fetchall()
            named: list[tuple[UUID, list[str]]] = []
            scattered = None
            for option, kind, label in options:
                if kind == "write_in":
                    scattered = option
                else:
                    named.append(
                        (
                            option,
                            [
                                YESNO.get(w, w)
                                for w in re.sub(
                                    r"[^a-z ]", " ", slug(label.replace("’", "'")).replace("-", " ")
                                ).split()
                                if w not in SUFFIXES
                            ],
                        )
                    )
            ballots: dict[str, UUID] = {}
            for name, write_in in group.select("candidate", "write_in").unique().iter_rows():
                if write_in or re.search(r"write-?in|scattering|\(W\)$", name, re.IGNORECASE):
                    if scattered is None:
                        scattered = cs.key("us.option", f"{contest_key}:write_in:scattered")
                        if scattered in cs.minted:
                            cs.add(
                                "contest_option",
                                id=scattered,
                                contest_id=contest,
                                kind="write_in",
                                ballot_label="Scattered",
                            )
                    ballots[name] = scattered
                    continue
                tokens = {
                    YESNO.get(w, w)
                    for w in re.sub(r"[^a-z ]", " ", slug(name.replace("’", "'")).replace("-", " ")).split()
                    if w not in SUFFIXES
                }
                hits = sorted(
                    ((len(tokens & set(words)), option) for option, words in named if words and words[-1] in tokens),
                    key=lambda hit: -hit[0],
                )
                if hits and (len(hits) == 1 or hits[0][0] > hits[1][0]):
                    ballots[name] = hits[0][1]
                else:
                    misses[f"{state}: candidate without option"] += 1
                    print(f"miss {state}: {contest_key}: no option for {name}")
            state_fips = FIPS[state]
            sums: dict[tuple[UUID, UUID], int] = {}
            for county, name, votes in group.select("county", "candidate", "votes").iter_rows():
                option = ballots.get(name)
                unit = place or (
                    counties.get((state_fips, plain(ALIASES.get((state, plain(county)), county))))
                    or counties.get((state_fips, plain(county)))
                    or counties.get((state_fips, county))
                )
                if option is None:
                    continue
                if unit is None:
                    misses[f"{state}: county without unit"] += 1
                    print(f"miss {state}: {contest_key}: no county {county}")
                    continue
                sums[(unit, option)] = sums.get((unit, option), 0) + votes
            if not any(sums.values()):
                stats["all-zero skipped"] += 1
                continue
            reporting = group.select(pl.col("reporting").max(), pl.col("total").max()).row(0)
            as_of = datetime.strptime(next(iter(pages[state].values())).rsplit("@", 1)[1], "%Y%m%dT%H%M%SZ").replace(
                tzinfo=UTC
            )
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            is_default = (
                conn.execute(
                    """select 1 from result_set
                       where contest_id = %s and status = 'active' and source_id <> %s
                         and (is_default or stage in ('official', 'certified')) limit 1""",
                    [contest, sources[state]],
                ).fetchone()
                is None
            )
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary",
                kind="counted",
                status="active",
                source_id=sources[state],
                as_of=as_of,
                is_default=is_default,
                completeness=reporting[0] / reporting[1] if reporting[1] else None,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            for (unit, option), votes in sums.items():
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
            if reporting[1]:
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="context",
                    body_md=f"{reporting[0]} of {reporting[1]} reporting units had reported.",
                )
            stats["sets staged"] += 1
        print(json.dumps({"stats": stats, "misses": misses}, indent=1))
        return cs.write()


def fallback(
    state: str,
    name: str,
    people: frozenset[str],
    places: list[str],
    published: dict[UUID, tuple[str, set[str]]],
    titles: dict[UUID, set[str]],
    towns: dict[str, set[str]],
    codes: dict[tuple[str, str], str],
) -> str | None:
    words = {re.sub(r"^0+(?=\d)|(?<=\d)(st|nd|rd|th)$", "", w) for w in slug(name).split("-") if w not in STOP}
    scored = []
    for contest, (key, wanted) in published.items():
        segment = key.split(":")[2]
        if re.fullmatch(r"\d{5}", segment) and (
            len(places) != 1 or codes.get((FIPS[state], plain(places[0]))) != segment
        ):
            continue
        if len(places) == 1 and not re.match(r"\d|sd|house$|senate$", segment):
            continue
        if people <= {"yes", "no"} and segment in towns and not towns[segment] & words:
            continue
        overlap = len(people & wanted) / len(people | wanted) if people | wanted else 0
        if overlap >= 0.6:
            scored.append(
                (
                    round(overlap, 2),
                    len(words & (titles.get(contest, set()) | set(re.split(r"[:-]", key)[2:]))),
                    contest,
                    key,
                )
            )
    scored.sort(key=lambda hit: (-hit[0], -hit[1]))
    best = [hit for hit in scored if hit[:2] == scored[0][:2]] if scored else []
    if len(best) != 1 or ((people <= {"yes", "no"} or len(people) < 2) and best[0][1] < 1):
        return None
    return best[0][3]
