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
PUBLISHER = "Board of Elections in the City of New York"
COUNTIES = {"New York": "36061", "Bronx": "36005", "Kings": "36047", "Queens": "36081", "Richmond": "36085"}
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
    "Unofficial election night results from the Board of Elections' election night reporting site, by assembly "
    "district within each borough, as of the time shown. The percentage of scanners reported is not stored. "
    "The Board's certified election district reports supersede these figures."
)


class Rows(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.rows: list[list[tuple[str, str | None, str | None]]] = []
        self.headings: list[str] = []
        self.cell: list[str] | None = None
        self.link: tuple[str | None, str | None] = (None, None)
        self.current: list[tuple[str, str | None, str | None]] | None = None
        self.heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = dict(attrs)
        if tag == "tr":
            self.current = []
        elif tag in ("td", "th"):
            self.cell = []
            self.link = (None, None)
            self.heading = tag == "th"
        elif tag == "a" and self.cell is not None:
            self.link = (values.get("href"), values.get("title"))

    def handle_endtag(self, tag: str) -> None:
        if tag in ("td", "th") and self.cell is not None:
            text = re.sub(r"\s+", " ", "".join(self.cell).replace("\xa0", " ")).strip()
            if self.heading and text:
                self.headings.append(text)
            if self.current is not None:
                self.current.append((text, *self.link))
            self.cell = None
        elif tag == "tr" and self.current is not None:
            self.rows.append(self.current)
            self.current = None

    def handle_data(self, data: str) -> None:
        if self.cell is not None:
            self.cell.append(data)


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
    pages: dict[str, str] = {}
    seen: set[str] = set()
    level = [urljoin(BASE, "index.html")]
    client = httpx.Client(follow_redirects=True, timeout=60, headers={"User-Agent": "electionsarchive.com"})

    def get(url: str) -> tuple[str, bytes | None]:
        for attempt in range(3):
            response = client.get(url)
            if response.status_code == 404:
                return url, None
            if response.status_code < 400:
                return url, response.content
            time.sleep(1 + attempt)
        return url, None

    while level:
        seen.update(level)
        with ThreadPoolExecutor(6) as pool:
            fetched = list(pool.map(get, level))
        level = []
        for url, content in fetched:
            if content is None:
                print(f"missing {url}")
                continue
            key = f"{url}@{stamp}"
            raw.store(SOURCE, key, content, suffix=".html", retrieved_at=datetime.now(UTC), media_type="text/html")
            pages[url] = key
            parser, _ = read(content.decode("latin-1"))
            for row in parser.rows:
                for _, href, title in row:
                    if href is None or not href.endswith(".html") or href.startswith(("http", "javascript")):
                        continue
                    target = urljoin(url, href)
                    ad_row = bool(re.fullmatch(r"AD\s*\d+", row[0][0]))
                    if (
                        target not in seen
                        and target not in level
                        and not ad_row
                        and (title or "").lower() != "total"
                        and not (title or "").startswith(("Home", "Back"))
                        and not re.fullmatch(r"C\d\.html", href)
                    ):
                        level.append(target)
    PARSED.mkdir(parents=True, exist_ok=True)
    (PARSED / "pages.json").write_text(json.dumps(pages))
    print(f"{len(pages)} pages")


def parse() -> None:
    pages = json.loads((PARSED / "pages.json").read_text())
    out = []
    for url, key in pages.items():
        content = raw.cached(SOURCE, key).path.read_bytes().decode("latin-1")
        parser, as_of = read(content)
        headings = [h for h in parser.headings if h not in ("BOARD OF ELECTIONS IN THE CITY OF NEW YORK",)]
        headings = [h for h in headings if not h.startswith(("GENERAL", "PRIMARY", "SPECIAL", "UNOFFICIAL"))]
        if not headings:
            continue
        contest = headings[0]
        area = headings[1] if len(headings) > 1 else ""
        rows = [[(text.replace("&Affordable;", "&Affordable"), href, title) for text, href, title in row] for row in parser.rows]
        rows = [[cell for cell in row if cell[0]] for row in rows]
        header = next((i for i, row in enumerate(rows) if row and "Reported *" in [c[0] for c in row]), None)
        if header is None:
            continue
        labels = [c[0] for c in rows[header]]
        if labels[0] == "Reported *":
            names = [c[0] for c in rows[header - 1]]
            lines = labels[1:]
            candidates = [(n, l.strip("()")) for n, l in zip([n for n in names if n != "WRITE-IN"], lines, strict=False)]
            if "WRITE-IN" in names:
                candidates.append(("WRITE-IN", ""))
        else:
            candidates = [(n, "") for n in labels[: labels.index("Reported *")]]
        for row in rows[header + 1 :]:
            match = re.fullmatch(r"AD\s*(\d+)", row[0][0])
            if match is None or len(row) < 2 + len(candidates):
                continue
            reported = float(row[1][0].rstrip("%")) if row[1][0].endswith("%") else None
            for (name, line), cell in zip(candidates, row[2:], strict=False):
                if cell[0].isdigit():
                    out.append((url, contest, area, int(match[1]), reported, name, line, int(cell[0]), as_of))
    pl.DataFrame(
        out,
        schema=["page", "contest", "area", "ad", "reported", "candidate", "line", "votes", "as_of"],
        orient="row",
    ).write_parquet(PARSED / "results.parquet")
    print(f"{len(out)} values")


def target(title: str) -> tuple[str, str] | None:
    def number(text: str) -> str | None:
        found = re.search(r"(\d+)(?:st|nd|rd|th)", text)
        return found[1] if found else None

    if title in STATE_SEATS:
        return STATE_PATH, STATE_SEATS[title]
    if title in ("Mayor", "Public Advocate"):
        return CITY_PATH, slug(title)
    if title == "City Comptroller":
        return CITY_PATH, "comptroller"
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
        return CITY_PATH, f"council-district-{n}"
    if title.startswith("Justice of the Supreme Court"):
        return CITY_PATH, f"supreme-court-{n}-{slug(COUNTY_OF_DISTRICT[n])}"
    if title.startswith("Surrogate"):
        return CITY_PATH, f"surrogate-{slug(title.removeprefix('Surrogate').strip())}"
    if title.startswith("Judge of the Civil Court - County"):
        return CITY_PATH, f"civil-court-{slug(title.removeprefix('Judge of the Civil Court - County').strip())}"
    if title.startswith("Judge of the Civil Court - District"):
        county = title.rsplit(" - ", 1)[1]
        return CITY_PATH, f"civil-court-district-{n}-{slug(county)}"
    if title.startswith("Proposal Number"):
        heading = re.sub(r"^Proposal Number \d+, an? \w+: ", "", title)
        return CITY_PATH, f"proposal-{slug(heading)[:48].rstrip('-')}"
    return None


def emit():
    frame = pl.read_parquet(PARSED / "results.parquet")
    pages = json.loads((PARSED / "pages.json").read_text())
    with connect() as conn:
        cs = Changeset(
            conn,
            pipeline=SOURCE,
            version=VERSION,
            summary=f"New York City's unofficial election night results of {DAY} by assembly district from the Board of Elections' election night reporting site",
            polity="US",
        )
        city_polity = cs.key("us.polity", "new-york-city")
        known_parties = {
            slug(text): value
            for value, text in conn.execute(
                "select x.value, n.text from external_id x join name n on n.entity_id = x.entity_id "
                "where x.scheme = 'us.party' and x.value like 'NY:%' order by x.value desc"
            ).fetchall()
        }
        if conn.execute("select 1 from scheme where code = 'us.nyc_ad'").fetchone() is None:
            raise ValueError("the us.nyc_ad scheme must already exist")
        units: dict[tuple[str, int], UUID] = {}
        added: set[UUID] = set()
        unmatched: list[str] = []
        for title, group in frame.group_by("contest", maintain_order=True):
            title = title[0]
            mapped = target(title)
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
            owned = path == CITY_PATH
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
                    words = [w for w in re.sub(r"[^a-z ]", "", slug(name.split(" / ")[0]).replace("-", " ")).split() if w not in SUFFIXES]
                    roots[label if kind in ("answer", "write_in") else (words[0], words[-1]) if words else name] = option
            keys = dict(
                conn.execute(
                    "select entity_id, value from external_id where scheme = 'us.option' and entity_id = any(%s)",
                    [list(roots.values())],
                ).fetchall()
            )
            source_title = f"Unofficial Election Night Results {DAY}: {title}"
            source = cs.source(PUBLISHER, source_title)
            if source in cs.minted:
                cs.add("source", id=source, kind="official_body", publisher=PUBLISHER, title=source_title, url=BASE, license="Public record")
            for page in group["page"].unique():
                cs.artifact(raw.cached(SOURCE, pages[page]), source)
            result_set = cs.key(f"{SOURCE}.result_set", contest_key)
            as_of = group["as_of"].max()
            cs.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="preliminary",
                kind="counted",
                status="active",
                source_id=source,
                as_of=as_of,
                is_default=owned,
            )
            cs.add("note", entity_id=result_set, kind="methodology", body_md=NOTE)
            ballots: dict[tuple[str, str], UUID] = {}
            for name, line in group.select("candidate", "line").unique().iter_rows():
                if name in ("YES", "NO"):
                    root = roots.get(name.title())
                    if root is not None:
                        ballots[(name, line)] = root
                    continue
                if name == "WRITE-IN":
                    root = roots.get("Scattered") or roots.get("Write-in")
                    if root is None:
                        root_key = f"{contest_key}:write_in:scattered"
                        root = cs.key("us.option", root_key)
                        if root in cs.minted and root not in added:
                            added.add(root)
                            cs.add("contest_option", id=root, contest_id=contest, kind="write_in", ballot_label="Scattered")
                    ballots[(name, line)] = root
                    continue
                words = [w for w in re.sub(r"[^a-z ]", "", slug(name.split(" / ")[0]).replace("-", " ")).split() if w not in SUFFIXES]
                root = roots.get((words[0], words[-1])) if words else None
                if root is None:
                    unmatched.append(f"{title}: no option for {name}")
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
            for ad, borough, name, line, votes in group.select("ad", "area", "candidate", "line", "votes").iter_rows():
                option = ballots.get((name, line))
                fips = COUNTIES.get(borough)
                if option is None or fips is None:
                    continue
                code = f"{fips}:{ad:02d}"
                if (code, ad) not in units:
                    units[(code, ad)] = cs.unit("us.nyc_ad", code, DAY)
                    if units[(code, ad)] in cs.minted:
                        cs.add(
                            "reporting_unit",
                            id=units[(code, ad)],
                            polity_id=city_polity,
                            scheme_code="us.nyc_ad",
                            code=code,
                            parent_id=cs.unit("us.county", fips),
                            area_relation="within",
                            area_id=cs.area("us.sldl", f"NY:{ad}"),
                            kind="standard",
                            valid_from=DAY,
                            valid_to=DAY + timedelta(days=1),
                        )
                cs.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=units[(code, ad)],
                    option_id=option,
                    channel=0,
                    measure=1,
                    count_round=0,
                    flags=0,
                    value=votes,
                )
        for line in unmatched:
            print("unmatched:", line)
        return cs.write()
