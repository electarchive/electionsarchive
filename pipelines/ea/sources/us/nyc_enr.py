import json
import os
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from html.parser import HTMLParser
from urllib.parse import urljoin
from uuid import UUID
from zoneinfo import ZoneInfo

import httpx
import polars as pl

from ea import raw
from ea.changeset import Changeset, slug
from ea.store import DATA, connect

SOURCE = "us.nyc_enr"
VERSION = "1"
PARSED = DATA / "parsed" / SOURCE
BASE = os.environ.get("NYC_ENR_BASE", "https://enr.boenyc.gov/")
DAY = date.fromisoformat(os.environ.get("NYC_ENR_DAY", "2026-11-03"))
WAYBACK = os.environ.get("NYC_ENR_WAYBACK")
PUBLISHER = "Board of Elections in the City of New York"
COUNTIES = {"New York": "36061", "Bronx": "36005", "Kings": "36047", "Queens": "36081", "Richmond": "36085"}
BOROUGHS = {
    "New York": "Manhattan",
    "Bronx": "Bronx",
    "Kings": "Brooklyn",
    "Queens": "Queens",
    "Richmond": "Staten Island",
}
COUNTY_OF_DISTRICT = {"1": "New York", "2": "Kings", "11": "Queens", "12": "Bronx", "13": "Richmond"}
PARTIES = {
    "Democratic": "democratic-party",
    "Republican": "republican-party",
    "Conservative": "NY:conservative-party",
    "Working Families": "NY:working-families-party",
    "Save Our City": "NY:save-our-city",
    "Libertarian": "libertarian-party",
    "Green": "green-party",
}
STATE_SEATS = {
    "Governor/Lieutenant Governor": "governor",
    "Attorney General": "attorney-general",
    "State Comptroller": "comptroller",
    "Comptroller": "comptroller",
}
CITY_PATH = f"us/ny/new-york-city/{os.environ.get('NYC_ENR_CITY_SLUG', f'{DAY}-general')}"
STATE_PATH = f"us/ny/{os.environ.get('NYC_ENR_STATE_SLUG', f'{DAY}-general')}"
SUFFIXES = {"jr", "sr", "ii", "iii", "iv"}
NOTE = (
    "Unofficial election night results from the Board of Elections' election night reporting site, as of the time "
    "shown. The Board's certified results by election district supersede these figures."
)


class Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, str | None, str | None]]] = []
        self.headings: list[str] = []
        self.open: list[dict] = []
        self.depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "table":
            self.depth += 1
        elif tag == "tr":
            if self.open and self.open[-1]["depth"] == self.depth:
                self.rows.append(self.open.pop()["row"])
            self.open.append({"row": [], "cell": None, "link": (None, None), "heading": False, "depth": self.depth})
        elif tag in ("td", "th") and self.open:
            self.open[-1].update(cell=[], link=(None, None), heading=tag == "th")
        elif tag == "a" and self.open and self.open[-1]["cell"] is not None:
            self.open[-1]["link"] = (values.get("href"), values.get("title"))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.open and self.open[-1]["cell"] is not None:
            frame = self.open[-1]
            text = re.sub(r"\s+", " ", "".join(frame["cell"]).replace("\xa0", " ")).strip()
            if frame["heading"] and text:
                self.headings.append(text)
            frame["row"].append((text, *frame["link"]))
            frame["cell"] = None
        elif tag == "tr" and self.open and self.open[-1]["depth"] == self.depth:
            self.rows.append(self.open.pop()["row"])
        elif tag == "table":
            while self.open and self.open[-1]["depth"] >= self.depth:
                self.rows.append(self.open.pop()["row"])
            self.depth -= 1

    def handle_data(self, data: str) -> None:
        if self.open and self.open[-1]["cell"] is not None:
            self.open[-1]["cell"].append(data)


def read(content: str):
    parser = Rows()
    parser.feed(content)
    as_of = re.search(r"Information As Of: ([0-9: -]+) (E[SD]T)", content)
    return parser, (
        datetime.strptime(as_of[1].strip(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("America/New_York"))
        if as_of
        else None
    )


def fetch() -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    manifest = PARSED / f"pages-{DAY}.json"
    pages: list[list[str]] = json.loads(manifest.read_text()) if manifest.exists() else []
    stored = {key for _, key in pages}
    client = httpx.Client(follow_redirects=True, timeout=60, headers={"User-Agent": "electionsarchive.com"})

    def get(url: str) -> tuple[str, bytes | None]:
        for attempt in range(4):
            try:
                response = client.get(url)
            except httpx.HTTPError:
                time.sleep(2 + 2 * attempt)
                continue
            if response.status_code == 404:
                return url, None
            if response.status_code < 400:
                return url, response.content
            time.sleep(2 + 2 * attempt)
        return url, None

    def keep(url: str, key: str, content: bytes) -> None:
        if key in stored:
            return
        raw.store(SOURCE, key, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
        pages.append([url, key])
        stored.add(key)

    if WAYBACK:
        start, end = WAYBACK.split("-")
        for attempt in range(6):
            response = client.get(
                "http://web.archive.org/cdx/search/cdx",
                params={
                    "url": "enr.boenyc.gov",
                    "matchType": "prefix",
                    "from": start,
                    "to": end,
                    "fl": "timestamp,original,statuscode,digest",
                },
                timeout=180,
            )
            if response.status_code == 200:
                break
            time.sleep(30 * (attempt + 1))
        response.raise_for_status()
        cdx = response.text.split("\n")
        captures: dict[tuple[str, str], tuple[str, str]] = {}
        for line in cdx:
            fields = line.split()
            if len(fields) != 4 or fields[2] != "200":
                continue
            found = re.fullmatch(r"https?://enr\.boenyc\.gov/([A-Za-z0-9]+\.html)(?:\?.*)?", fields[1])
            if found is not None:
                captures.setdefault((found[1], fields[3]), (fields[0], fields[1]))
        for (name, _), (timestamp, original) in sorted(captures.items(), key=lambda item: item[1][0]):
            _, content = get(f"http://web.archive.org/web/{timestamp}id_/{original}")
            if content is None:
                print(f"missing {name}@{timestamp}")
            else:
                keep(urljoin(BASE, name), f"{urljoin(BASE, name)}@{timestamp}", content)
            time.sleep(0.5)
    else:
        seen: set[str] = set()
        level = [BASE]
        while level:
            seen.update(level)
            with ThreadPoolExecutor(6) as pool:
                fetched = list(pool.map(get, level))
            level = []
            for url, content in fetched:
                if content is None:
                    print(f"missing {url}")
                    continue
                keep(url, f"{url}@{stamp}", content)
                parser, _ = read(content.decode("latin-1"))
                for row in parser.rows:
                    for _, href, title in row:
                        if href is None or not href.endswith(".html") or href.startswith(("http", "javascript")):
                            continue
                        target = urljoin(url, href)
                        if (
                            target not in seen
                            and target not in level
                            and (title or "").lower() != "total"
                            and not (title or "").startswith(("Home", "Back"))
                            and not re.fullmatch(r"C\d\.html", href)
                        ):
                            level.append(target)
    PARSED.mkdir(parents=True, exist_ok=True)
    manifest.write_text(json.dumps(pages))
    print(f"{len(pages)} pages")


def parse() -> None:
    out = []
    for url, key in json.loads((PARSED / f"pages-{DAY}.json").read_text()):
        content = raw.cached(SOURCE, key).path.read_bytes().decode("latin-1")
        parser, as_of = read(content)
        headings = [h for h in parser.headings if h != "BOARD OF ELECTIONS IN THE CITY OF NEW YORK"]
        headings = [h for h in headings if not h.startswith(("GENERAL", "PRIMARY", "SPECIAL", "UNOFFICIAL"))]
        if not headings or as_of is None:
            continue
        district = re.search(r"\[ AD (\d+) \]", headings[0])
        contest = re.sub(r"\(Vote for[^)]*\)|\[ AD \d+ \]", "", headings[0]).strip()
        area = headings[1] if len(headings) > 1 and headings[1] in COUNTIES else None
        rows = [
            [(text.replace("&Affordable;", "&Affordable"), href, title) for text, href, title in row]
            for row in parser.rows
        ]
        if "Totals by Candidate" in headings:
            scanners = re.search(r"Percentage of Scanners Reported: ([\d.]+) %", content)
            for row in rows:
                cells = [c[0] for c in row]
                if cells and cells[0] == "Totals by Candidate":
                    break
                if len(cells) >= 6 and cells[0].isdigit() and cells[4].isdigit():
                    out.append(
                        (
                            url,
                            key,
                            contest,
                            "city",
                            None,
                            None,
                            None,
                            float(scanners[1]) if scanners else None,
                            cells[2],
                            cells[3],
                            int(cells[4]),
                            as_of,
                        )
                    )
            continue
        rows = [[cell for cell in row if cell[0]] for row in rows]
        header = next((i for i, row in enumerate(rows) if row and "Reported *" in [c[0] for c in row]), None)
        if header is None:
            continue
        labels = [c[0] for c in rows[header]]
        if labels[0] == "Reported *":
            names = [c[0] for c in rows[header - 1]]
            lines = labels[1:]
            candidates = [
                (n, l.strip("()")) for n, l in zip([n for n in names if n != "WRITE-IN"], lines, strict=False)
            ] or [(n, "") for n in names if n != "WRITE-IN"]
            if "WRITE-IN" in names:
                candidates.append(("WRITE-IN", ""))
        else:
            candidates = [(n, "") for n in labels[: labels.index("Reported *")]]
        for row in rows[header + 1 :]:
            first = row[0][0] if row else ""
            ad = re.fullmatch(r"AD\s*(\d+)", first)
            ed = re.fullmatch(r"ED\s*(\d+)", first)
            if first == "Total" and district is not None and len(row) >= 1 + len(candidates):
                cells, level, unit = row[1:], "ad", (area, int(district[1]), None)
                reported = None
            elif (ad or ed or first in COUNTIES) and len(row) >= 2 + len(candidates):
                cells = row[2:]
                reported = float(row[1][0].rstrip("%")) if row[1][0].endswith("%") else None
                if ed is not None and district is not None:
                    level, unit = "ed", (None, int(district[1]), int(ed[1]))
                elif ad is not None:
                    level, unit = "ad", (area, int(ad[1]), None)
                elif ad is None and ed is None:
                    level, unit = "borough", (first, None, None)
                else:
                    continue
            else:
                continue
            for (name, line), cell in zip(candidates, cells, strict=False):
                if cell[0].isdigit():
                    out.append((url, key, contest, level, *unit, reported, name, line, int(cell[0]), as_of))
    pl.DataFrame(
        out,
        schema={
            "page": pl.String,
            "key": pl.String,
            "contest": pl.String,
            "level": pl.String,
            "borough": pl.String,
            "ad": pl.Int64,
            "ed": pl.Int64,
            "scanners": pl.Float64,
            "candidate": pl.String,
            "line": pl.String,
            "votes": pl.Int64,
            "as_of": pl.Datetime("us", "America/New_York"),
        },
        orient="row",
    ).write_parquet(PARSED / f"results-{DAY}.parquet")
    print(f"{len(out)} values")


def target(title: str, boroughs: set[str]) -> tuple[str, str] | None:
    def number(text: str) -> str | None:
        found = re.search(r"(\d+)(?:st|nd|rd|th)", text)
        return found[1] if found else None

    if title in STATE_SEATS:
        return STATE_PATH, STATE_SEATS[title]
    if title in ("Mayor", "Public Advocate"):
        return CITY_PATH, slug(title)
    if title == "City Comptroller":
        return CITY_PATH, "comptroller"
    if "Borough President" in title or title.startswith("President of the Borough"):
        named = [v for k, v in BOROUGHS.items() if k.lower() in title.lower() or v.lower() in title.lower()] or [
            BOROUGHS[c] for c in COUNTIES if c in boroughs
        ]
        return (CITY_PATH, f"borough-president-{slug(named[0])}") if len(named) == 1 else None
    rest = re.sub(
        r"^(District Attorney|Surrogate|Judge of the Civil Court - (County|District)|Justice of the Supreme Court)",
        "",
        title,
    ).strip()
    if (
        title.startswith(("District Attorney", "Surrogate", "Judge of the Civil Court", "Justice of the Supreme Court"))
        and not rest
    ):
        return None
    if title.startswith("District Attorney"):
        return CITY_PATH, f"district-attorney-{slug(title.removeprefix('District Attorney').strip())}"
    n = number(title)
    if title.startswith("Member of the Assembly"):
        return STATE_PATH, f"assembly-{n}"
    if title.startswith("State Senator"):
        return STATE_PATH, f"state-senate-{n}"
    if title.startswith("Representative in Congress"):
        return STATE_PATH, f"house-{n}"
    if title.startswith("Member of the City Council"):
        if n is None:
            return None
        return CITY_PATH, f"council-district-{n}"
    if title.startswith("Justice of the Supreme Court"):
        if n not in COUNTY_OF_DISTRICT:
            return None
        return CITY_PATH, f"supreme-court-{n}-{slug(COUNTY_OF_DISTRICT[n])}"
    if title.startswith("Surrogate"):
        return CITY_PATH, f"surrogate-{slug(title.removeprefix('Surrogate').strip())}"
    if title.startswith("Judge of the Civil Court - County"):
        return CITY_PATH, f"civil-court-{slug(title.removeprefix('Judge of the Civil Court - County').strip())}"
    if title.startswith("Judge of the Civil Court - District"):
        county = title.rsplit(" - ", 1)[1]
        return CITY_PATH, f"civil-court-district-{n}-{slug(county)}"
    if title.startswith("Proposal Number"):
        state = re.match(r"Proposal Number (\d+), an Amendment:", title)
        if state is not None:
            return STATE_PATH, f"proposition-{state[1]}"
        heading = re.sub(r"^Proposal Number \d+, an? \w+: ", "", title)
        return CITY_PATH, f"proposal-{slug(heading)[:48].rstrip('-')}"
    return None


def emit():
    frame = pl.read_parquet(PARSED / f"results-{DAY}.parquet").filter(
        pl.col("as_of").dt.date().is_between(DAY, DAY + timedelta(days=2))
    )
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"New York City's unofficial election night results of {DAY} from the Board of Elections' election night reporting site, one result set per contest, update, and level (citywide, borough, assembly district, and election district)",
            polity="US",
        )
        city_polity = cs.key("us.polity", "new-york-city")
        city_unit = cs.unit("us.place", "3651000")
        city_place = cs.area("us.place", "3651000")
        known_parties = {
            slug(text): value
            for value, text in conn.execute(
                "select x.value, n.text from external_id x join name n on n.entity_id = x.entity_id "
                "where x.scheme = 'us.party' and x.value like 'NY:%' order by x.value desc"
            ).fetchall()
        }
        if conn.execute("select 1 from scheme where code = 'us.nyc_ad'").fetchone() is None:
            raise ValueError("the us.nyc_ad scheme must already exist")
        districts = {
            code: (area, county)
            for code, area, county in conn.execute(
                """select c.code, c.area_id, u.id from area_code c
                   join area a on a.id = c.area_id and a.scheme_code = 'us.nyc_ed'
                   join area_parent p on p.area_id = c.area_id and p.valid @> %s::date
                   join area_code pc on pc.area_id = p.parent_id
                   join reporting_unit u on u.scheme_code = 'us.county' and u.code = pc.code
                   where c.valid @> %s::date""",
                [DAY, DAY],
            ).fetchall()
        }
        borough_of_ad = {
            ad: borough
            for ad, borough in frame.filter(pl.col("level") == "ad", pl.col("borough").is_not_null())
            .select("ad", "borough")
            .unique()
            .iter_rows()
        }
        units: dict[tuple[str, str], UUID | None] = {}
        added: set[UUID] = set()
        unmatched: list[str] = []
        skipped = 0

        def unit(level: str, borough: str | None, ad: int | None, ed: int | None) -> UUID | None:
            if level == "city":
                return city_unit
            if level == "borough":
                return cs.unit("us.county", COUNTIES[borough]) if borough in COUNTIES else None
            if level == "ad":
                borough = borough or borough_of_ad.get(ad)
                if borough not in COUNTIES:
                    return None
                code = f"{COUNTIES[borough]}:{ad:02d}"
                if ("ad", code) not in units:
                    units[("ad", code)] = cs.unit("us.nyc_ad", code, DAY)
                    if units[("ad", code)] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=units[("ad", code)],
                            polity_id=city_polity,
                            scheme_code="us.nyc_ad",
                            code=code,
                            parent_id=cs.unit("us.county", COUNTIES[borough]),
                            area_relation="within",
                            area_id=cs.area("us.sldl", f"NY:{ad}"),
                            kind="standard",
                            valid_from=DAY,
                            valid_to=DAY + timedelta(days=1),
                        )
                return units[("ad", code)]
            code = f"{ad:02d}{ed:03d}"
            if ("ed", code) not in units:
                found = cs.unit("us.nyc_ed", code, DAY)
                if found in cs.minted:
                    if code not in districts:
                        cs.minted.discard(found)
                        units[("ed", code)] = None
                        return None
                    area, parent = districts[code]
                    cs.add(
                        "reporting_unit",
                        id=found,
                        polity_id=city_polity,
                        scheme_code="us.nyc_ed",
                        code=code,
                        parent_id=parent,
                        area_relation="equals",
                        area_id=area,
                        kind="standard",
                        valid_from=DAY,
                        valid_to=DAY + timedelta(days=1),
                    )
                    cs.add(
                        "reporting_unit_area",
                        unit_id=found,
                        area_id=city_place,
                        valid_from=DAY,
                        valid_to=DAY + timedelta(days=1),
                    )
                units[("ed", code)] = found
            return units[("ed", code)]

        for (title,), group in frame.group_by("contest", maintain_order=True):
            mapped = target(title, set(group.filter(pl.col("level") == "borough")["borough"].to_list()))
            if mapped is None:
                unmatched.append(f"{title}: no contest mapping")
                continue
            path, seat = mapped
            found = conn.execute(
                """select s.entity_id from slug s join entity e on e.id = s.entity_id and e.retired_by is null
                   where s.path = %s""",
                [f"{path}/{seat}"],
            ).fetchone()
            if found is None:
                unmatched.append(f"{title}: no contest at {path}/{seat}")
                continue
            contest = found[0]
            election, contest_key = conn.execute(
                """select c.election_id, x.value from contest c join external_id x on x.entity_id = c.id and x.scheme = 'us.contest'
                   where c.id = %s limit 1""",
                [contest],
            ).fetchone()
            settled = conn.execute(
                "select 1 from result_set where contest_id = %s and stage in ('official', 'certified') and status = 'active'",
                [contest],
            ).fetchone()
            owned = path == CITY_PATH and settled is None
            boroughs = set(group.filter(pl.col("level") == "borough")["borough"].to_list()) or {
                c for c in COUNTIES if title.endswith(c)
            }
            if (
                path == STATE_PATH
                or seat in ("mayor", "public-advocate", "comptroller")
                or seat.startswith("proposal-")
            ):
                pass
            elif len(boroughs) == 1:
                group = group.with_columns(
                    borough=pl.when(pl.col("level") == "city")
                    .then(pl.lit(next(iter(boroughs))))
                    .otherwise(pl.col("borough")),
                    level=pl.when(pl.col("level") == "city").then(pl.lit("borough")).otherwise(pl.col("level")),
                )
            elif group.filter(pl.col("level") == "borough").height:
                group = group.filter(pl.col("level") != "city")
            stamps = sorted(group["as_of"].unique().to_list())
            frames: dict[datetime, datetime] = {}
            cluster: list[datetime] = []
            for stamp in stamps:
                if cluster and stamp - cluster[-1] > timedelta(minutes=5):
                    frames.update({s: cluster[-1] for s in cluster})
                    cluster = []
                cluster.append(stamp)
            frames.update({s: cluster[-1] for s in cluster})
            group = group.with_columns(frame=pl.col("as_of").replace_strict(frames)).sort("as_of")
            newest = max(frames.values())
            roots = {}
            children = {}
            for option, parent, label, kind, ballot_name, party in conn.execute(
                """select o.id, o.parent_option_id, o.ballot_label, o.kind, ca.ballot_name, o.party_id
                   from contest_option o left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
                   where o.contest_id = %s""",
                [contest],
            ).fetchall():
                if parent is not None:
                    children[(parent, party)] = option
                else:
                    name = ballot_name or label or ""
                    words = [
                        w
                        for w in re.sub(r"[^a-z ]", "", slug(name.split(" / ")[0]).replace("-", " ")).split()
                        if w not in SUFFIXES
                    ]
                    roots[label if kind in ("answer", "write_in") else (words[0], words[-1]) if words else name] = (
                        option
                    )
            keys = dict(
                conn.execute(
                    "select entity_id, value from external_id where scheme = 'us.option' and entity_id = any(%s)",
                    [list(roots.values())],
                ).fetchall()
            )
            source_title = f"Unofficial Election Night Results {DAY}: {title}"
            source = cs.source(PUBLISHER, source_title)
            if source in cs.minted:
                cs.add(
                    "source",
                    id=source,
                    kind="official_body",
                    publisher=PUBLISHER,
                    title=source_title,
                    url=BASE,
                    license="Public record",
                )
            ballots: dict[tuple[str, str], UUID | None] = {}

            for name, line in group.select("candidate", "line").unique().iter_rows():
                if name in ("YES", "NO"):
                    ballots[(name, line)] = roots.get(name.title())
                    continue
                if name == "WRITE-IN":
                    root = roots.get("Scattered") or roots.get("Write-in")
                    if root is None:
                        root = cs.key("us.option", f"{contest_key}:write_in:scattered")
                        if root in cs.minted and root not in added:
                            added.add(root)
                            cs.add(
                                "contest_option", id=root, contest_id=contest, kind="write_in", ballot_label="Scattered"
                            )
                    ballots[(name, line)] = root
                    continue
                words = [
                    w
                    for w in re.sub(r"[^a-z ]", "", slug(name.split(" / ")[0]).replace("-", " ")).split()
                    if w not in SUFFIXES
                ]
                root = roots.get((words[0], words[-1])) if words else None
                if root is None:
                    unmatched.append(f"{title}: no option for {name}")
                    ballots[(name, line)] = None
                    continue
                if not line:
                    ballots[(name, line)] = root
                    continue
                first = line.split("/")[0]
                party_key = PARTIES.get(first, known_parties.get(slug(first), f"NY:{slug(first)}"))
                party = cs.key("us.party", party_key)
                if party in cs.minted and party not in added:
                    added.add(party)
                    cs.add("party", id=party, kind="party", polity_id=cs.polity("US-NY"))
                    cs.add("name", entity_id=party, text=first, lang="en", kind="official", is_preferred=True)
                child = children.get((root, party))
                if child is None:
                    child = cs.key("us.option", f"{keys.get(root, f'{contest_key}:{slug(name)}')}:{slug(line)}")
                    if child in cs.minted and child not in added:
                        added.add(child)
                        cs.add(
                            "contest_option",
                            id=child,
                            contest_id=contest,
                            parent_option_id=root,
                            kind="ticket",
                            party_id=party,
                            ballot_label=f"{name} ({line})",
                        )
                ballots[(name, line)] = child
            for (moment, level), rows in group.group_by("frame", "level", maintain_order=True):
                default = owned and level == "ad" and moment == newest
                result_set = cs.key(f"{SOURCE}.result_set", f"{contest_key}@{moment:%Y%m%dT%H%M%S}:{level}")
                if result_set not in cs.minted and moment != newest:
                    was = conn.execute("select is_default from result_set where id = %s", [result_set]).fetchone()
                    if was is not None and was[0] == default:
                        skipped += 1
                        continue
                for key in rows["key"].unique():
                    cs.artifact(raw.cached(SOURCE, key), source)
                cs.add(
                    "result_set",
                    id=result_set,
                    election_id=election,
                    contest_id=contest,
                    stage="preliminary",
                    kind="counted",
                    status="active",
                    source_id=source,
                    as_of=moment,
                    is_default=default,
                )
                shares = {
                    "citywide" if level == "city" else BOROUGHS.get(borough or "", borough): share
                    for borough, share in rows.filter(pl.col("scanners").is_not_null())
                    .select("borough", "scanners")
                    .iter_rows()
                    if level == "city" or (level == "borough" and borough is not None)
                }
                cs.add(
                    "note",
                    entity_id=result_set,
                    kind="methodology",
                    body_md=NOTE
                    + (
                        " Share of scanners reported at this time: "
                        + ", ".join(f"{name} {share:.2f}%" for name, share in shares.items())
                        + "."
                        if shares
                        else ""
                    ),
                )
                values: dict[tuple[UUID, UUID], int] = {}
                for borough, ad, ed, name, line, votes in rows.select(
                    "borough", "ad", "ed", "candidate", "line", "votes"
                ).iter_rows():
                    choice = ballots.get((name, line))
                    place = unit(level, borough, ad, ed)
                    if choice is not None and place is not None:
                        values[(place, choice)] = votes
                for (place, choice), votes in values.items():
                    cs.add(
                        "result_value",
                        result_set_id=result_set,
                        unit_id=place,
                        option_id=choice,
                        channel=0,
                        measure=1,
                        count_round=0,
                        flags=0,
                        value=votes,
                    )
        for line in sorted(set(unmatched)):
            print("unmatched:", line)
        print(f"{skipped} published sets left as they are")
        return cs.write()
