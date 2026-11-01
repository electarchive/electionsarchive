import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urljoin
from uuid import UUID

import httpx

from ea.changeset import slug
from ea.sources.us.nyc_enr import BASE, COUNTIES, DAY, STATE_PATH, STATE_SEATS, SUFFIXES, read, target
from ea.store import Connection

FOLLOW = re.compile(r"(OF\w+AD0PY\d+|CD\d+ADI0)\.html")
CONTEST = re.compile(r"CD\d+ADI0\.html")
RING = ["36059", "36119"]


def collect(client: httpx.Client) -> dict[str, tuple[bytes, str]]:
    def get(url: str) -> tuple[str, bytes | None]:
        try:
            response = client.get(url)
        except httpx.HTTPError:
            return url, None
        return url, response.content if response.status_code == 200 else None

    pages: dict[str, tuple[bytes, str]] = {}
    level = [urljoin(BASE, "index.html")]
    seen = set(level)
    with ThreadPoolExecutor(8) as pool:
        while level:
            found = []
            for url, content in pool.map(get, level):
                if content is None:
                    continue
                pages[url] = (content, ".html")
                parser, _ = read(content.decode("latin-1"))
                for row in parser.rows:
                    for _, href, _ in row:
                        link = urljoin(url, href) if href is not None and FOLLOW.fullmatch(href) else None
                        if link is not None and link not in seen:
                            seen.add(link)
                            found.append(link)
            level = found
    return pages if any(CONTEST.search(url) for url in pages) else {}


def words(name: str) -> list[str]:
    return [
        w for w in re.sub(r"[^a-z ]", "", slug(name.split(" / ")[0]).replace("-", " ")).split() if w not in SUFFIXES
    ]


def inside(conn: Connection, feed: str, contest: UUID) -> bool:
    cached = conn.execute(
        "select target from live.map where feed_id = %s and kind = 'contest' and label = %s", [feed, str(contest)]
    ).fetchone()
    if cached is not None:
        return cached[0] is not None
    share = conn.execute(
        """select coalesce(max(st_area(st_intersection(g.geom, r.geom)) / st_area(g.geom)), 0)
           from contest c join area_geometry g on g.area_id = c.district_area_id and g.valid @> %s::date
           join reporting_unit u on u.scheme_code = 'us.county' and u.code = any(%s)
           join area_geometry r on r.area_id = u.area_id and r.valid @> %s::date and st_intersects(g.geom, r.geom)
           where c.id = %s""",
        [DAY, RING, DAY, contest],
    ).fetchone()
    found = share is None or share[0] < 0.001
    conn.execute(
        """insert into live.map (feed_id, kind, label, target) values (%s, 'contest', %s, %s)
           on conflict (feed_id, kind, scope, label) do update set target = excluded.target""",
        [feed, str(contest), contest if found else None],
    )
    return found


def plan(
    conn: Connection, feed: str, paths: dict[str, Path]
) -> tuple[list[tuple[str, str, UUID, dict[tuple[UUID, UUID], int], tuple[int, int], str | None]], list[str]]:
    boroughs = dict(
        conn.execute(
            "select code, id from reporting_unit where scheme_code = 'us.county' and code = any(%s)",
            [list(COUNTIES.values())],
        ).fetchall()
    )
    tallies = []
    unmatched: list[str] = []
    for url, path in sorted(paths.items()):
        if not CONTEST.search(url):
            continue
        content = path.read_bytes().decode("latin-1")
        parser, as_of = read(content)
        headings = [
            h
            for h in parser.headings
            if h != "BOARD OF ELECTIONS IN THE CITY OF NEW YORK"
            and not h.startswith(("GENERAL", "PRIMARY", "SPECIAL", "UNOFFICIAL"))
        ]
        if not headings:
            continue
        title = re.sub(r"\(Vote for[^)]*\)|\[ AD \d+ \]", "", headings[0]).strip()
        rows = [
            [(text.replace("&Affordable;", "&Affordable"), href, label) for text, href, label in row if text]
            for row in parser.rows
        ]
        header = next((i for i, row in enumerate(rows) if row and "Reported *" in [c[0] for c in row]), None)
        if header is None:
            continue
        labels = [c[0] for c in rows[header]]
        if labels[0] == "Reported *":
            above = [c[0] for c in rows[header - 1]]
            named = [n for n in above if n != "WRITE-IN"]
            names = (named[: len(labels) - 1] if len(labels) > 1 else named) + (
                ["WRITE-IN"] if "WRITE-IN" in above else []
            )
        else:
            names = labels[: labels.index("Reported *")]
        places = {
            row[0][0]: [int(cell[0]) if cell[0].isdigit() else 0 for cell in row[2 : 2 + len(names)]]
            for row in rows[header + 1 :]
            if row and row[0][0] in COUNTIES and len(row) >= 2 + len(names)
        }
        mapped = target(title, set(places))
        if mapped is None:
            unmatched.append(f"{title}: no contest mapping")
            continue
        path_, seat = mapped
        found = conn.execute(
            """select s.entity_id, x.value from slug s join entity e on e.id = s.entity_id and e.retired_by is null
               join external_id x on x.entity_id = s.entity_id and x.scheme = 'us.contest'
               where s.path = %s limit 1""",
            [f"{path_}/{seat}"],
        ).fetchone()
        if found is None:
            unmatched.append(f"{title}: no contest at {path_}/{seat}")
            continue
        contest, contest_key = found
        if path_ == STATE_PATH and (seat in STATE_SEATS.values() or not inside(conn, feed, contest)):
            unmatched.append(f"{title}: reaches past the city, left to a statewide feed")
            continue
        roots: dict[str | tuple[str, str], UUID] = {}
        for option, label, kind, ballot_name in conn.execute(
            """select o.id, o.ballot_label, o.kind, ca.ballot_name from contest_option o
               left join candidacy ca on ca.option_id = o.id and ca.role = 'head'
               where o.contest_id = %s and o.parent_option_id is null""",
            [contest],
        ).fetchall():
            name = ballot_name or label or ""
            split = words(name)
            roots[label if kind in ("answer", "write_in") else (split[0], split[-1]) if split else name] = option
        sums: dict[tuple[UUID, UUID], int] = {}
        for index, name in enumerate(names):
            if name in ("YES", "NO"):
                option = roots.get(name.title())
            elif name == "WRITE-IN":
                option = roots.get("Scattered") or roots.get("Write-in")
                if option is None:
                    continue
            else:
                split = words(name)
                option = roots.get((split[0], split[-1])) if split else None
            if option is None:
                unmatched.append(f"{title}: no option for {name}")
                continue
            for borough, votes in places.items():
                key = (boroughs[COUNTIES[borough]], option)
                sums[key] = sums.get(key, 0) + votes[index]
        tallies.append(("NY", contest_key, contest, sums, (0, 0), as_of.isoformat() if as_of else None))
    return tallies, unmatched
