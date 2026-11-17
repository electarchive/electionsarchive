"""Czech Chamber of Deputies elections of 1996 and 1998 by polling district from the volby.cz results pages.

The 1996 and 1998 elections have no open-data files, only the results service's HTML pages, which print every
polling district (okrsek) by obec, okres, and electoral region (kraj), every candidate list with its candidates and
preference votes, and the seat allocation tables. Each election is one package, `cz.volby/ps-<year>`, built on
`cz.volby/geo` and `cz.volby/registry`. The eight electoral regions of the time (the regions of 1960 to 2000) are not
the 14 regions of today, so `ps-1996` holds their scheme, their shapes (unions of the municipalities that voted in
each), the electoral system, and the parties, and `ps-1998` depends on it.
"""

import html
import re
from collections import Counter, defaultdict
from datetime import date

import polars as pl
import shapely

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.cz.volby import (
    BASE,
    CHAMBER_BODY,
    CZECHIA,
    SOURCE,
    TERRITORY,
    VOLBY_SOURCE,
    _accounting,
    _overcount,
    _places,
    area,
    clean,
    key,
)

PS = {
    1996: ("ps1996", date(1996, 5, 31), date(1996, 6, 1)),
    1998: ("ps1998", date(1998, 6, 19), date(1998, 6, 20)),
}
PAGES = {1990: "cnr1990", 1992: "cnr1992", 1996: "ps1996", 1998: "ps1998"}
STATIC = ("u1", "u3", "u4", "u5", "u52", "u53", "u6", "u64", "u65", "u66", "u67", "u68", "u69", "u610")
VOTES, PREFERENCE = 1, 7
ACCOUNTING = ((2, 3), (14, 4), (3, 6), (4, 7))
KRAJ_SCHEME = "cz.kraj_1960"
FIRST = min(PS)
METHOD = (
    "Counted by the Czech Statistical Office per polling district (okrsek), as printed on the volby.cz results pages. "
    "Registered voters are the voters on the roll, voters are the envelopes issued, ballots cast are the envelopes "
    "put in the box, and valid votes are the valid list votes. Each candidate's preference votes are the totals for "
    "the electoral region, as the pages print them. Polling districts are placed in their municipality; where the "
    "municipality's code no longer exists, in its district or region."
)
SYSTEM_METHOD = (
    "200 deputies were elected for four years in eight electoral regions (the regions of 1960 to 2000) from "
    "candidate lists. A list needed 5% of the valid votes in the whole country, and a coalition of lists 7% to 11% "
    "by its size. The first scrutiny gave each region's seats by the quota of the region's valid votes divided by "
    "its seats plus one, and the second scrutiny gave the seats still open to the lists' remainders nationally. The "
    "regions' seat counts were set by dividing each region's valid votes by the Republic mandate number. Voters "
    "could mark preference votes, and a candidate with enough of them moved up the list."
)
RULES: dict[str, object] = {
    "formula": "hagenbach_bischoff_quota",
    "remainders": "national",
    "threshold": {"level": "list", "scope": "national", "percent": 5},
    "lists": {"order": "flexible", "vacancy": "next_on_list"},
}
SYSTEM = key("electoral_system", "ps", 1996)
KRAJE = {
    "31": "Praha",
    "32": "Středočeský kraj",
    "33": "Jihočeský kraj",
    "34": "Západočeský kraj",
    "35": "Severočeský kraj",
    "36": "Východočeský kraj",
    "37": "Jihomoravský kraj",
    "38": "Severomoravský kraj",
}
WORKERS = 12


def link(year: int | str, name: str, **query: object) -> str:
    tail = "&".join(f"{k}={v}" for k, v in query.items())
    return f"{BASE}/pls/{PAGES[year] if isinstance(year, int) else year}/{name}" + (f"?{tail}" if tail else "")


def read(year: int | str, name: str, **query: object) -> str:
    return raw.cached(SOURCE, link(year, name, **query)).path.read_text(encoding="utf-8")


def rows(text: str) -> list[list[str]]:
    """Each table row of a results page as its cell texts, with entities decoded and spaces folded."""
    out: list[list[str]] = []
    for row in re.split(r"<tr[^>]*>", text, flags=re.IGNORECASE)[1:]:
        row = re.split(r"</tr>", row, flags=re.IGNORECASE)[0]
        cells = re.findall(r"<t[dh][^>]*>(.*?)</t[dh]>", row, re.DOTALL | re.IGNORECASE)
        out.append([clean(html.unescape(re.sub(r"<[^>]+>", " ", cell)).replace("\xa0", " ")) for cell in cells])
    return out


def whole(text: str) -> int:
    return int(text.replace(" ", ""))


def fetch() -> None:
    for year in PS:
        fetch_year(year)


def fetch_year(year: int | str) -> None:
    """Every results page of one election, which the packages cite."""
    for name in STATIC:
        raw.fetch(SOURCE, link(year, name), suffix=".html")
    tree = read(year, "u53")
    okresy = sorted(set(re.findall(r"u5312\?xkraj=(\d+)&xokres=(\d+)", tree)), key=lambda p: (int(p[0]), int(p[1])))
    raw.fetch_many(
        SOURCE,
        [link(year, "u5312", xkraj=k, xokres=o) for k, o in okresy]
        + [link(year, "u5311", xkraj=k, xokres=o) for k, o in okresy],
        suffix=".html",
        workers=WORKERS,
    )
    obce = obec_list(year)
    raw.fetch_many(
        SOURCE,
        [link(year, "u53111", xkraj=k, xokres=o, xobec=c) for k, o, c in obce],
        suffix=".html",
        workers=WORKERS,
    )
    raw.fetch_many(
        SOURCE,
        [link(year, "u531111", xkraj=k, xokres=o, xobec=c, xokrsek=n) for k, o, c, n in okrsek_list(year)],
        suffix=".html",
        workers=WORKERS,
    )
    raw.fetch(SOURCE, link(year, "u32", xpl=0, xtr=2), suffix=".html")
    raw.fetch_many(
        SOURCE,
        [link(year, "u321", xpl=0, xtr=2, xvstrana=v, xkraj=k) for v, k in list_pages(year)],
        suffix=".html",
        workers=WORKERS,
    )


def okres_list(year: int | str) -> list[tuple[str, str]]:
    tree = read(year, "u53")
    return sorted(set(re.findall(r"u5312\?xkraj=(\d+)&xokres=(\d+)", tree)), key=lambda p: (int(p[0]), int(p[1])))


def obec_list(year: int | str) -> list[tuple[str, str, str]]:
    found: list[tuple[str, str, str]] = []
    for k, o in okres_list(year):
        found += re.findall(r"u53111\?xkraj=(\d+)&xokres=(\d+)&xobec=(\d+)", read(year, "u5311", xkraj=k, xokres=o))
    return found


def okrsek_list(year: int | str) -> list[tuple[str, str, str, str]]:
    found: list[tuple[str, str, str, str]] = []
    for k, o, c in obec_list(year):
        text = read(year, "u53111", xkraj=k, xokres=o, xobec=c)
        found += [(k, o, c, n) for n in re.findall(r"xokrsek=(\d+)", text)]
    return found


def list_pages(year: int | str) -> list[tuple[str, str]]:
    text = read(year, "u32", xpl=0, xtr=2)
    return [
        (v, k)
        for v, k in dict.fromkeys(re.findall(r"u321\?xpl=0&xtr=2&xvstrana=(\d+)&xkraj=(\d+)", text))
        if v != "00" and k != "00"
    ]


def packages() -> list[str]:
    return [f"cz.volby/ps-{year}" for year in PS]


def parse() -> None:
    for year in PS:
        chamber(year).write()


def emit() -> None:
    package.run("load", packages())


def numbers(cells: list[str]) -> bool:
    return bool(cells) and all(re.fullmatch(r"[\d. ]+", cell) for cell in cells)


def tally(year: int | str, k: str, o: str, c: str, n: str) -> tuple[dict[int, int], dict[str, int], dict[str, str]]:
    """One polling district's accounting by measure, valid votes by list number, and the list names it prints."""
    accounting: dict[int, int] = {}
    votes: dict[str, int] = {}
    names: dict[str, str] = {}
    for cells in rows(read(year, "u531111", xkraj=k, xokres=o, xobec=c, xokrsek=n)):
        if len(cells) == 9 and numbers(cells[:2]) and numbers(cells[3:5]):
            accounting = {measure: whole(cells[at].split(".")[0]) for measure, at in ACCOUNTING}
        elif len(cells) >= 4 and re.fullmatch(r"\d\d", cells[0]) and re.fullmatch(r"\d+", cells[2].replace(" ", "")):
            sides = [cells[:4]] + ([cells[5:9]] if len(cells) >= 9 else [])
            for number, name, count, _ in sides:
                if name and count:
                    votes[number] = whole(count)
                    names[number] = name
    if not accounting:
        raise ValueError(f"ps {year}: no accounting row for {k}/{o}/{c}/{n}")
    return accounting, votes, names


def regional(year: int | str) -> dict[str, dict[str, int]]:
    """Valid votes per list per region, as the regional results page prints them."""
    out: dict[str, dict[str, int]] = {k: {} for k in KRAJE}
    for cells in rows(read(year, "u5")):
        if "hlasy" in cells and len(cells) >= 12:
            at = cells.index("hlasy")
            for k, count in zip(KRAJE, cells[at + 2 : at + 10], strict=True):
                if count:
                    out[k][f"{int(cells[at - 2]):02d}"] = whole(count)
    return out


def regions(year: int | str) -> dict[str, dict[int, int]]:
    """Accounting per region from the territorial overview."""
    out: dict[str, dict[int, int]] = {}
    for cells in rows(read(year, "u52")):
        if len(cells) == 11 and cells[0] in KRAJE and numbers(cells[2:4]):
            out[cells[0]] = {2: whole(cells[5]), 14: whole(cells[6]), 3: whole(cells[8]), 4: whole(cells[9])}
    return out


def national(year: int | str) -> tuple[dict[int, int], dict[str, tuple[str, int]]]:
    accounting: dict[int, int] = {}
    lists: dict[str, tuple[str, int]] = {}
    for cells in rows(read(year, "u4")):
        if len(cells) >= 9 and numbers(cells[:2]) and numbers(cells[3:5]):
            accounting = {2: whole(cells[3]), 14: whole(cells[4]), 3: whole(cells[6]), 4: whole(cells[7])}
        elif len(cells) >= 4 and re.fullmatch(r"\d\d", cells[0]) and re.fullmatch(r"[\d ]+", cells[2]):
            for number, name, count, _ in [cells[:4]] + ([cells[5:9]] if len(cells) >= 9 else []):
                if name and count:
                    lists[number] = (name, whole(count))
    return accounting, lists


def full_names(year: int | str) -> dict[str, str]:
    """The full names of the lists that passed the first threshold, and the short forms of all lists."""
    out: dict[str, str] = {}
    for cells in rows(read(year, "u65")):
        if len(cells) == 3 and re.fullmatch(r"\d\d", cells[0]):
            out[cells[0]] = cells[1]
    return out


def short_names(year: int | str) -> dict[str, str]:
    out: dict[str, str] = {}
    for cells in rows(read(year, "u64")):
        if len(cells) >= 3 and re.fullmatch(r"\d\d", cells[0]) and not numbers(cells[1:2]):
            out[cells[0]] = cells[1]
    return out


def seats(year: int | str) -> tuple[dict[str, int], dict[str, int]]:
    """The seats of each region and of each list as the allocation tables print them."""
    by_region: dict[str, int] = {}
    for cells in rows(read(year, "u67")):
        if len(cells) >= 5 and cells[0] in KRAJE and numbers(cells[2:3]):
            by_region[cells[0]] = int(cells[-1])
    by_list: dict[str, int] = {}
    for cells in rows(read(year, "u610")):
        if len(cells) == 10 and re.fullmatch(r"\d\d", cells[0]) and cells[-1].isdigit():
            by_list[cells[0]] = int(cells[-1])
    return by_region, by_list


def candidates(year: int | str, list_number: str, k: str) -> list[dict[str, object]]:
    out: list[dict[str, object]] = []
    for cells in rows(read(year, "u321", xpl=0, xtr=2, xvstrana=list_number, xkraj=k)):
        if len(cells) == 7 and cells[0].isdigit() and numbers(cells[2:5]):
            out.append(
                {
                    "position": int(cells[0]),
                    "name": cells[1],
                    "age": int(cells[2]),
                    "preference": whole(cells[3]),
                    "elected": cells[5] == "*",
                }
            )
    return out


def names_of(year: int | str) -> tuple[dict[tuple[str, str], str], dict[str, str]]:
    """The names of the places the pages print: obec by (region, code) and okres by (region, number)."""
    obce: dict[tuple[str, str], str] = {}
    for k, o in okres_list(year):
        for cells in rows(read(year, "u5311", xkraj=k, xokres=o)):
            if len(cells) >= 2 and re.fullmatch(r"\d{6}", cells[0]):
                obce[(k, cells[0])] = cells[1]
    return obce, {}


def old_regions(p: Package, groups: dict[str, list[str]]) -> None:
    """The eight regions of 1960 to 2000 with shapes made from the current districts that voted in each."""
    build = package.folder("cz.volby/geo") / "build"
    geometry = pl.read_parquet(build / "area_geometry.parquet").select("area_id", "geom")
    shapes = {row["area_id"]: shapely.from_wkb(row["geom"]) for row in geometry.iter_rows(named=True)}
    artifact = raw.cached("cz.cuzk", "https://services.cuzk.gov.cz/shp/stat/epsg-5514/1.zip")
    p.add(
        "scheme",
        code=KRAJ_SCHEME,
        polity_id=CZECHIA,
        kind="electoral_district",
        rank=1,
        noun="region",
        code_system=None,
    )
    p.add(
        "boundary_set",
        id=key("boundary_set", KRAJ_SCHEME, 1960),
        scheme_code=KRAJ_SCHEME,
        vintage="1960",
        valid_from=date(1960, 7, 11),
        valid_to=date(2000, 1, 1),
        as_of=artifact.retrieved_at.date(),
    )
    for k, title in KRAJE.items():
        id = area(KRAJ_SCHEME, k)
        p.add("area", id=id, scheme_code=KRAJ_SCHEME)
        p.add("area_code", area_id=id, code=k)
        p.add("area_parent", area_id=id, parent_id=TERRITORY)
        p.name(id, title, "cs", "official")
        members = [shapes[district] for district in groups[k] if district in shapes]
        merged = shapely.union_all([shapely.make_valid(shape) for shape in members])
        merged = shapely.MultiPolygon(
            [part for part in shapely.get_parts(merged) if shapely.get_type_id(part) in (3, 6)]
        )
        merged = shapely.set_srid(merged, 4326)
        p.add(
            "area_geometry",
            area_id=id,
            geom=shapely.to_wkb(merged, hex=True, include_srid=True),
            label_point=shapely.to_wkb(
                shapely.set_srid(shapely.point_on_surface(merged), 4326), hex=True, include_srid=True
            ),
            quality="synthetic",
            source_id=RUIAN_SOURCE,
        )


RUIAN_SOURCE = key("source", "cz.cuzk")


def placement(year: int | str) -> tuple[dict[str, str], dict[tuple[str, str], str], dict[str, set[str]]]:
    """Where each municipality that voted sits today.

    Returns each municipality's area where the geo package holds it, the current district that most of each
    1996-style district's municipalities lie in, and the current districts each region's municipalities lie in.
    """
    build = package.folder("cz.volby/geo") / "build"
    places = _places()
    towns = set(places.filter(pl.col("scheme_code") == "cz.obec")["code"])
    wards = set(places.filter(pl.col("scheme_code") == "cz.mestska_cast")["code"])
    parents = pl.read_parquet(build / "area_parent.parquet")
    parent = dict(zip(parents["area_id"], parents["parent_id"], strict=True))
    found: dict[str, str] = {}
    by_group: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    by_district: dict[str, Counter[str]] = defaultdict(Counter)
    for k, o, c in obec_list(year):
        if c in towns:
            here = area("cz.obec", c)
        elif c in wards:
            here = area("cz.mestska_cast", c)
        else:
            continue
        found[c] = here
        district = parent[here]
        if c in wards:
            district = parent[district]
        by_group[(k, o)][district] += 1
        by_district[district][k] += 1
    group = {pair: counted.most_common(1)[0][0] for pair, counted in by_group.items()}
    members: dict[str, set[str]] = {k: set() for k in KRAJE}
    for district, counted in by_district.items():
        members[counted.most_common(1)[0][0]].add(district)
    return found, group, members


def chamber(year: int) -> Package:
    _, first, last = PS[year]
    p = Package(
        f"cz.volby/ps-{year}",
        SOURCE,
        f"The {year} Czech Chamber of Deputies election by polling district from the volby.cz results pages",
        depends=["cz.volby/geo", "cz.volby/registry", *([] if year == FIRST else [f"cz.volby/ps-{FIRST}"])],
    )
    p.external |= {CZECHIA, TERRITORY}
    found, group, members = placement(year)
    if year == FIRST:
        old_regions(p, {k: sorted(v) for k, v in members.items()})
        p.add(
            "electoral_system",
            id=SYSTEM,
            polity_id=CZECHIA,
            valid_from=date(1996, 1, 1),
            valid_to=date(2002, 1, 1),
            rules=RULES,
            rules_version=1,
        )
        p.add("note", id=p.id("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=SYSTEM_METHOD, lang="en")
    election = key("election", "ps", year)
    p.add("election", id=election, polity_id=CZECHIA, kind="general", date_from=first, date_to=last, status="held")
    p.name(election, f"{year} Czech parliamentary election", "en", "official")
    p.name(election, f"Volby do Poslanecké sněmovny {year}", "cs", "native", False)
    path = f"cz/{first}-general"
    p.add("slug", path=path, entity_id=election, is_current=True)

    total, printed = national(year)
    by_kraj = regional(year)
    declared_kraj = regions(year)
    seats_kraj, seats_list = seats(year)
    full, short = full_names(year), short_names(year)
    earlier_full = full_names(FIRST) if year != FIRST else {}
    earlier = {name: no for no, name in earlier_full.items()}
    lists = sorted(printed)
    party_of: dict[str, str] = {}
    for number in lists:
        borrowed = earlier.get(full.get(number, ""))
        party_of[number] = p.id("party", "ps", FIRST if borrowed else year, borrowed or number)
        if borrowed:
            continue
        p.add("party", id=party_of[number], kind="party", polity_id=CZECHIA)
        p.add("external_id", entity_id=party_of[number], scheme="cz.volby.ps", value=f"{year}:{number}")
        p.name(party_of[number], full.get(number, printed[number][0]), "cs", "official")
        if number in full and printed[number][0] != full[number]:
            p.name(party_of[number], printed[number][0], "cs", "short", False)
        if number in short:
            p.name(party_of[number], short[number], "cs", "abbreviation", False)

    pages = list_pages(year)
    for name in (*STATIC, "u32"):
        query = {"xpl": 0, "xtr": 2} if name == "u32" else {}
        p.artifact(raw.cached(SOURCE, link(year, name, **query)), VOLBY_SOURCE)
    for v, k in pages:
        p.artifact(raw.cached(SOURCE, link(year, "u321", xpl=0, xtr=2, xvstrana=v, xkraj=k)), VOLBY_SOURCE)
    for k, o in okres_list(year):
        for name in ("u5311", "u5312"):
            p.artifact(raw.cached(SOURCE, link(year, name, xkraj=k, xokres=o)), VOLBY_SOURCE)

    roster = {(number, k): candidates(year, number, k) for number, k in pages}
    won: Counter[str] = Counter()
    for (_, k), rows_ in roster.items():
        won[k] += sum(1 for row in rows_ if row["elected"])
    contest_of: dict[str, str] = {}
    set_of: dict[str, str] = {}
    option_of: dict[tuple[str, str], str] = {}
    system = SYSTEM
    for k, title in KRAJE.items():
        contest = contest_of[k] = key("contest", "ps", year, k)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=CHAMBER_BODY,
            district_area_id=area(KRAJ_SCHEME, k),
            magnitude=won[k],
            round_number=1,
            electoral_system_id=system,
            default_result_kind="counted",
        )
        p.name(contest, title, "cs", "official")
        p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
        result_set = set_of[k] = key("result_set", "ps", year, k)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=VOLBY_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for measure, value in declared_kraj[k].items():
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
        running = sorted({v for v, here in pages if here == k} | set(by_kraj[k]))
        for number in running:
            option = option_of[(k, number)] = key("option", "ps", year, k, number)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party_of[number],
                ballot_position=int(number),
                ballot_label=printed[number][0],
            )
            if number in by_kraj[k]:
                p.add("declared", result_set_id=result_set, option_id=option, measure=VOTES, value=by_kraj[k][number])
    elected: Counter[tuple[str, str]] = Counter()
    for number, k in pages:
        option = option_of[(k, number)]
        for row in roster[(number, k)]:
            tag = f"ps:{year}:{k}:{number}:{row['position']}"
            person = p.id("person", tag)
            p.add("person", id=person)
            p.name(person, str(row["name"]), "cs", "official")
            candidacy = p.id("candidacy", tag)
            p.add(
                "candidacy",
                id=candidacy,
                person_id=person,
                option_id=option,
                role="list_member",
                list_position=row["position"],
                ballot_name=row["name"],
            )
            line = p.id("option", tag)
            p.add(
                "contest_option",
                id=line,
                contest_id=contest_of[k],
                kind="candidate",
                parent_option_id=option,
                ballot_position=row["position"],
                ballot_label=row["name"],
            )
            if row["preference"]:
                p.add(
                    "declared",
                    result_set_id=set_of[k],
                    option_id=line,
                    measure=PREFERENCE,
                    value=row["preference"],
                )
            if row["elected"]:
                elected[(k, number)] += 1
                p.add("outcome", result_set_id=set_of[k], option_id=option, candidacy_id=candidacy, kind="elected")
    for (k, number), n in sorted(elected.items()):
        p.add("outcome", result_set_id=set_of[k], option_id=option_of[(k, number)], kind="won", seats=n)
    if sum(won.values()) != sum(seats_kraj.values()):
        raise ValueError(f"ps {year}: {sum(won.values())} elected, {sum(seats_kraj.values())} seats printed")
    for k, expected in seats_kraj.items():
        if won[k] != expected:
            p.add(
                "note",
                id=key("note", set_of[k], "second"),
                entity_id=set_of[k],
                kind="caveat",
                body_md=f"The seat table prints {expected} seats for this region, while the candidate lists mark "
                f"{won[k]} deputies elected in it. The seats still sum to 200 nationally and match the table for "
                f"each list. The contest holds the {won[k]} seats marked elected.",
                lang="en",
            )
    for number, expected in seats_list.items():
        got = sum(n for (_, here), n in elected.items() if here == number)
        if got != expected:
            raise ValueError(f"ps {year} list {number}: {got} elected, {expected} seats printed")

    stats: dict[str, dict[int, int]] = {k: Counter() for k in KRAJE}
    sums: dict[str, Counter[str]] = {k: Counter() for k in KRAJE}
    per_okres: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    pairs: list[tuple[str, dict[str, str | None]]] = []
    for k, o, c, n in okrsek_list(year):
        accounting, votes, _ = tally(year, k, o, c, n)
        p.artifact(raw.cached(SOURCE, link(year, "u531111", xkraj=k, xokres=o, xobec=c, xokrsek=n)), VOLBY_SOURCE)
        code = f"{first}:{c}:{int(n)}"
        unit = key("unit", code)
        place = found.get(c) or group.get((k, o)) or area(KRAJ_SCHEME, k)
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=CZECHIA,
            scheme_code="cz.okrsek",
            code=code,
            area_relation="within",
            area_id=place,
            kind="standard",
        )
        for measure, value in accounting.items():
            stats[k][measure] += value
            per_okres[(k, o)][str(measure)] += value
        row: dict[str, str | None] = {
            "VOL_SEZNAM": str(accounting[2]),
            "VYD_OBALKY": str(accounting[14]),
            "ODEVZ_OBAL": str(accounting[3]),
            "PL_HL_CELK": str(accounting[4]),
        }
        pairs.append((set_of[k], row))
        _accounting(p, set_of[k], unit, row)
        for number, count in votes.items():
            sums[k][number] += count
            per_okres[(k, o)][f"list{number}"] += count
            p.add(
                "result_value",
                result_set_id=set_of[k],
                unit_id=unit,
                option_id=option_of[(k, number)],
                channel=0,
                measure=VOTES,
                count_round=0,
                flags=0,
                value=count,
            )
    _overcount(p, pairs)
    p.accept(
        "accounting.declared",
        "volby.cz prints each candidate's preference votes only as a total for the electoral region, not per "
        "polling district, so those totals are declared with no polling-district values to sum",
    )
    for k in KRAJE:
        if dict(stats[k]) != declared_kraj[k]:
            raise ValueError(f"ps {year} region {k}: units sum to {dict(stats[k])}, region prints {declared_kraj[k]}")
        if dict(sums[k]) != by_kraj[k]:
            raise ValueError(f"ps {year} region {k}: units' list votes differ from the region's")
    summed = {m: sum(stats[k][m] for k in KRAJE) for m in total}
    if summed != total:
        raise ValueError(f"ps {year}: regions sum to {summed}, the country prints {total}")
    for k, o in okres_list(year):
        want: Counter[str] = Counter()
        for cells in rows(read(year, "u5312", xkraj=k, xokres=o)):
            if len(cells) >= 9 and numbers(cells[:2]) and numbers(cells[3:5]):
                want.update({"2": whole(cells[3]), "14": whole(cells[4]), "3": whole(cells[6]), "4": whole(cells[7])})
            elif len(cells) >= 4 and re.fullmatch(r"\d\d", cells[0]) and re.fullmatch(r"[\d ]+", cells[2]):
                for number, _, count, _ in [cells[:4]] + ([cells[5:9]] if len(cells) >= 9 else []):
                    if count:
                        want[f"list{number}"] += whole(count)
        have = Counter({name: count for name, count in per_okres[(k, o)].items() if count or name in want})
        if {name: count for name, count in want.items() if count} != {n: c for n, c in have.items() if c}:
            raise ValueError(f"ps {year} district {k}/{o}: units differ from the district's printed totals")
    return p
