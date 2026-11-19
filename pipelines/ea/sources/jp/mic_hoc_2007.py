"""Japan's 21st House of Councillors regular election of 29 July 2007, by prefecture and, where the scans allow, by
counting district.

The ministry's 結果調 for 2007 gives every prefecture's district candidates, party and list-candidate votes for
proportional representation, and turnout, as workbooks and a PDF book. One package, `jp.mic_hoc_2007/2007-07-29`,
holds the 47 district contests and the national list contest at the prefecture level from those. The tables by
counting district are scanned images only; `jp.mic_hoc_scans` reads them, and a prefecture's district contest gets a
second, finer result set by counting district when its reads pass every test in `counting`. Parties, people, the
House, the electoral systems, the source, and the prefecture and municipal units are the ones the other House of
Councillors pipelines key, so 2007 joins the same records; a party, person, or unit the archive lacks is minted here
under the same key. Telling the two apart reads the database, so building the package needs one.
"""

import re
import subprocess
from collections import Counter
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from uuid import uuid5

from PIL import Image

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.sources.jp import mic, mic_hoc, mic_hoc_early, mic_hoc_scans
from ea.sources.jp.mic import dhondt, party_key, party_label, plain
from ea.sources.jp.mlit import PREFECTURES
from ea.store import DATA, connect

SOURCE = "jp.mic_hoc_2007"
RAW = mic.SOURCE
HELD = date(2007, 7, 29)
KEY = f"{SOURCE}/{HELD}"
BASE = mic_hoc_scans.BASE
XLS = mic_hoc_scans.XLS
SHEETS = ["2_1", "2_3", "3_5", "3_7", "3_8", "3_10", *(f"3_11_{n}" for n in range(1, 12)), "3_12"]
SEATS = (73, 48)
REGISTERED, BALLOTS, VALID, INVALID, VOTERS = 2, 3, 4, 5, 14
JAPAN = str(uuid5(package.NAMESPACE, "polity\x1fJP"))
NAMES = [native for native, _ in PREFECTURES]
SMD_METHOD = (
    "Candidate votes for the whole prefecture, with each candidate's result mark, from the ministry's table of "
    "district candidates (候補者別得票数（選挙区）) in the 結果調 book. Votes for candidates whose names other candidates "
    "share are apportioned (按分) and carry three decimals. Electors and voters come from the ministry's table of "
    "turnout by prefecture, and ballots cast, valid votes, and invalid votes from its table of ballots by prefecture. "
    "The marked winners are the candidates with the most votes for the district's seats."
)
PR_NATIONAL_METHOD = (
    "Party totals, party-only votes, personal votes, and seats from the ministry's national tables "
    "(党派別名簿登載者別得票数、当選人数 and 党派別議席配分表). Personal votes for candidates whose names other candidates "
    "share are apportioned (按分). The nationwide electors, voters, and ballots are the totals of the ministry's "
    "tables by prefecture."
)
PR_PLACE_METHOD = (
    "Votes by prefecture from the ministry's tables 都道府県別党派別得票数（比例代表） and the party tables of "
    "名簿登載者別得票数 by prefecture: each party's total, its party-only votes, and each candidate's personal votes, "
    "with electors, voters, and ballots by prefecture. The prefectures add up to the national totals."
)
VALID_GAP = (
    "the ministry's valid-vote count for proportional representation exceeds the sum of the party totals it prints "
    "in every prefecture (434 votes nationwide), and apportioned (按分) fractions are cut at three decimals, so the "
    "candidate votes of Nara, a single-seat district, fall short of its whole-number valid count by 0.037"
)
COUNTING_NOTE = (
    "The ministry publishes this election's results by counting district only as scanned images. District results "
    "by counting district are read from them for the prefectures whose scanned tables match the ministry's official "
    "prefecture totals exactly; the national list and the other prefectures are by prefecture only."
)
COUNTING_METHOD = (
    "Votes by counting district (開票区), read from the ministry's scanned tables of district candidates by counting "
    "district (候補者別開票区別得票数) in the 結果調, with two independent machine reads of every page and a third on "
    "some. A prefecture is included only when every figure is the same in every read or in a clear majority of "
    "them, each candidate's counting districts add up exactly to the ministry's prefecture total, and, where "
    "ballots are included, each counting district's candidates add up to its valid votes and its valid and invalid "
    "votes to its ballots cast. Each figure links to the scanned page it was read from."
)
PAGES = DATA / "pages" / SOURCE
DIGITIZED = 8
TURNOUT = (("s:ballots", BALLOTS), ("s:valid", VALID), ("s:invalid", INVALID))


@dataclass(frozen=True)
class Place:
    label: str
    scheme: str
    code: str
    piece: str | None
    cells: dict[str, tuple[Decimal, mic_hoc_scans.Source]]


def resolve(places: mic.Places, prefecture: str, label: str, previous: str | None) -> tuple[str, str, str | None]:
    try:
        return places.resolve(prefecture, label, previous)
    except LookupError:
        numbered = re.fullmatch(r"(\D+?)(\d+)", label)
        if numbered is None:
            raise
        scheme, code, _ = places.resolve(prefecture, f"{numbered[1]}市", previous)
        return scheme, code, numbered[2]


def counting(facts: mic_hoc_scans.Official) -> dict[int, list[Place]]:
    """The counting districts of every prefecture whose scanned district tables pass: each candidate cell is one value
    in every read or in a clear majority of at least two, each candidate's column adds up exactly to the official
    prefecture total, and every counting district resolves to a 2007 municipality, ward, or a numbered part of one.
    Ballots, valid, and invalid votes come along when they pass the same tests, and then every counting district's
    candidates must add up to its valid votes (less than one vote short, for apportioned fractions) and its valid and
    invalid votes to its ballots, or the prefecture is left out."""
    found: dict[int, list[mic_hoc_scans.Fragment]] = {}
    for fragment in mic_hoc_scans.fragments(raw.keys(mic_hoc_scans.SOURCE), facts):
        found.setdefault(fragment.document.prefecture, []).append(fragment)
    with connect() as conn:
        places = mic.Places(conn, HELD)
    passed: dict[int, list[Place]] = {}
    for number, fragments in sorted(found.items()):
        prefecture = mic_hoc_scans.observe(number, fragments)
        mic_hoc_scans.structure(prefecture)
        mic_hoc_scans.solve(prefecture, facts)
        leaves = list(prefecture.leaves)

        def read(key: str, row: int) -> tuple[Decimal, mic_hoc_scans.Source] | None:
            value = prefecture.values.get((key, row))
            seen = Counter(v for v, _ in prefecture.observed.get((key, row), []) if v is not None)
            if value is None or seen[value] < 2 or any(n >= seen[value] for v, n in seen.items() if v != value):
                return None
            return value, next(source for v, source in prefecture.observed[key, row] if v == value)

        def tied(keys: list[str]) -> bool:
            return all(
                sum((prefecture.values.get((key, row)) or Decimal(0) for row in leaves), Decimal(0))
                == facts.totals[number].get(key)
                for key in keys
            )

        candidates = [f"s{candidate.order}" for candidate in facts.smd[number]]
        turnout = [key for key, _ in TURNOUT]
        reads = {(key, row): read(key, row) for key in candidates + turnout for row in leaves}
        if not tied(candidates) or any(reads[key, row] is None for key in candidates for row in leaves):
            continue
        keys = candidates
        if tied(turnout) and all(reads[key, row] is not None for key in turnout for row in leaves):
            keys = candidates + turnout
            for row in leaves:
                votes = sum((prefecture.values[key, row] or Decimal(0) for key in candidates), Decimal(0))
                cast, valid, invalid = (prefecture.values[key, row] or Decimal(0) for key in turnout)
                if not -1 < votes - valid <= 0 or valid + invalid != cast:
                    keys = []
                    break
        if not keys:
            continue
        rows: list[Place] = []
        previous = None
        try:
            for row in leaves:
                label = mic_hoc_scans.place_label(prefecture.rows[row])
                scheme, code, piece = resolve(places, f"{number:02d}", label, previous)
                previous = code
                cells = {key: cell for key in keys if (cell := reads[key, row]) is not None}
                rows.append(Place(label, scheme, code, piece, cells))
        except LookupError:
            continue
        passed[number] = rows
    return passed


def files() -> list[str]:
    return [f"{BASE}/index.html", *(XLS.format(name) for name in SHEETS), mic_hoc_scans.BOOK]


def fetch() -> None:
    for url in files():
        raw.fetch(RAW, url, suffix=mic.suffix(url))


def parse() -> None:
    build().write()


def emit() -> None:
    package.run("load", [KEY])


def keyed(scheme: str, value: str) -> str:
    return str(uuid5(package.NAMESPACE, f"external_id:{scheme}\x1f{value}"))


def existing(pairs: set[tuple[str, str]]) -> dict[tuple[str, str], str]:
    with connect() as conn:
        found = {
            (scheme, value): str(entity)
            for scheme, value, entity in conn.execute(
                "select e.scheme, e.value, e.entity_id from external_id e join changeset c on c.id = e.changeset_id"
                " where c.pipeline <> %s and (e.scheme, e.value) in (select * from unnest(%s::text[], %s::text[]))",
                [SOURCE, [s for s, _ in pairs], [v for _, v in pairs]],
            ).fetchall()
        }
        merged: dict[str, str] = {}
        wanted = list(found.values())
        while wanted:
            step = dict(
                conn.execute(
                    "select entity_id::text, into_id::text from entity_merge where entity_id = any(%s::uuid[])",
                    [wanted],
                ).fetchall()
            )
            merged |= step
            wanted = [into for into in step.values() if into not in merged]
    for pair, entity in found.items():
        while entity in merged:
            entity = merged[entity]
        found[pair] = entity
    return found


def rows(name: str, columns: list[int]) -> dict[int, tuple[int, ...]]:
    path = raw.cached(RAW, XLS.format(name)).path
    found: dict[int, tuple[int, ...]] = {}
    for row in mic.grid(path, mic.sheets(path)[0]):
        label = row[0].replace(" ", "")
        number = 0 if label in ("計", "合計") else mic_hoc_scans.prefecture_of(label)
        if number is not None and all(row[column] for column in columns):
            found[number] = tuple(int(mic.amount(row[column])) for column in columns)
    return found


def build() -> Package:
    facts = mic_hoc_scans.official()
    found = mic_hoc.listing(21)
    held = HELD.isoformat()
    records, people = mic_hoc.national_lists(raw.cached(RAW, found.lists).path, held)
    records = [record for record in records if record["party"] != "合計"]
    seats = mic_hoc.seat_table(raw.cached(RAW, found.seats).path)
    accounts = {
        "smd": (rows("2_3", [3, 6]), rows("3_8", [1, 2, 3])),
        "pr": (rows("2_1", [3, 6]), rows("3_7", [1, 2, 3])),
    }
    check(facts, records, people, seats, accounts)

    def party_of(text: str) -> tuple[str, str] | None:
        label = party_label(text)
        if label in mic_hoc_early.WIKIDATA:
            return "wikidata", mic_hoc_early.WIKIDATA[label][0]
        return party_key(text, held)

    smd_people = {
        f"smd:{NAMES[number - 1]}:{plain(candidate.name)}"
        for number, candidates in facts.smd.items()
        for candidate in candidates
    }
    pr_people = {f"pr:{party_label(person['party'])}:{plain(person['name'])}" for person in people}
    party_pairs = {
        pair
        for text in [*(c.party for cs in facts.smd.values() for c in cs), *(r["party"] for r in records)]
        if (pair := party_of(text)) is not None
    }
    person_pairs = {(f"{mic_hoc.SOURCE}.person", key) for key in smd_people | pr_people}
    known = existing(party_pairs | person_pairs)
    districts = counting(facts)
    with connect() as conn:
        held_units = {
            str(row[0])
            for row in conn.execute(
                "select u.id from reporting_unit u join changeset c on c.id = u.changeset_id"
                " where c.pipeline <> %s and u.scheme_code in ('jp.municipality', 'jp.ward', %s)",
                [SOURCE, mic.PIECES],
            ).fetchall()
        }

    p = Package(
        KEY,
        SOURCE,
        "The 21st House of Councillors regular election of 29 July 2007 by prefecture, from the ministry's 結果調: "
        "district candidates, national list votes by party and candidate, turnout, and declared winners and seats",
    )
    p.accept("accounting.votes_valid", VALID_GAP)
    house = keyed("jp.body", "house-of-councillors")
    smd_system = keyed(f"{mic_hoc.SOURCE}.electoral_system", "hoc-sntv")
    pr_system = keyed(f"{mic_hoc.SOURCE}.electoral_system", "hoc-pr")
    source = str(uuid5(package.NAMESPACE, f"source\x1f{mic.PUBLISHER}\x1f{mic_hoc.TITLE}"))
    nation_area = str(uuid5(package.NAMESPACE, "area:jp.nation@None\x1fJP"))
    nation = str(uuid5(package.NAMESPACE, "unit:jp.nation@None\x1fJP"))
    p.external |= {JAPAN, house, smd_system, pr_system, source, nation_area, nation}
    for url in files():
        p.artifact(raw.cached(RAW, url), source)

    def prefecture(number: int) -> tuple[str, str]:
        unit = str(uuid5(package.NAMESPACE, f"unit:jp.prefecture@None\x1f{number:02d}"))
        area = str(uuid5(package.NAMESPACE, f"area:jp.prefecture@None\x1f{number:02d}"))
        p.external |= {unit, area}
        return unit, area

    minted: dict[tuple[str, str], str] = {}

    def entity(pair: tuple[str, str], table: str, text: str) -> str:
        if pair in minted:
            return minted[pair]
        if pair in known:
            p.external.add(known[pair])
            return known[pair]
        id = minted[pair] = keyed(*pair)
        if table == "party":
            p.add("party", id=id, kind="party", polity_id=JAPAN)
        else:
            p.add("person", id=id)
        p.name(id, text, "ja", "official")
        p.add("external_id", entity_id=id, scheme=pair[0], value=pair[1])
        return id

    def party(text: str) -> str | None:
        pair = party_of(text)
        return None if pair is None else entity(pair, "party", party_label(text))

    def person(key: str, text: str) -> str:
        return entity((f"{mic_hoc.SOURCE}.person", key), "person", text)

    values: dict[tuple[str, str, str | None, int], Decimal] = {}
    located: dict[tuple[str, str, str | None, int], tuple[str, tuple[float, float, float, float]]] = {}

    def value(result_set: str, unit: str, option: str | None, amount: Decimal | int, measure: int = 1) -> None:
        values[result_set, unit, option, measure] = Decimal(amount)

    added: set[str] = set()

    def unit_of(scheme: str, code: str, parent: str, area: str, relation: str, number: int) -> str:
        id = str(uuid5(package.NAMESPACE, f"unit:{scheme}@None\x1f{code}"))
        if id in held_units:
            p.external.add(id)
        elif id not in added:
            added.add(id)
            polity = str(uuid5(package.NAMESPACE, f"polity\x1fJP-{number:02d}"))
            p.external |= {area, polity, scheme}
            p.add(
                "reporting_unit",
                id=id,
                polity_id=polity,
                scheme_code=scheme,
                code=code,
                parent_id=parent,
                area_relation=relation,
                area_id=area,
                kind="standard",
                valid_from=None,
            )
        return id

    def place_unit(number: int, place: Place) -> str:
        area = str(uuid5(package.NAMESPACE, f"area:{place.scheme}@None\x1f{place.code}"))
        whole = unit_of(place.scheme, place.code, prefecture(number)[0], area, "equals", number)
        if place.piece is None:
            return whole
        return unit_of(mic.PIECES, f"{held}:{place.code}:{place.piece}", whole, area, "within", number)

    pages: dict[tuple[str, int], tuple[str, str]] = {}
    artifacts: dict[str, str] = {}

    def page(stem: str, number: int) -> tuple[str, str]:
        if (stem, number) in pages:
            return pages[stem, number]
        stored = raw.cached(RAW, f"{BASE}/pdf/{stem}.pdf")
        if stored.url not in artifacts:
            artifacts[stored.url] = p.artifact(stored, source)
        artifact = artifacts[stored.url]
        image = PAGES / f"{stored.sha256}-{number}.png"
        if not image.exists():
            PAGES.mkdir(parents=True, exist_ok=True)
            subprocess.run(
                ["pdftoppm", "-f", str(number), "-l", str(number), "-r", "100", "-gray", "-png", "-singlefile"]
                + [str(stored.path), str(image.with_suffix(""))],
                check=True,
            )
        width, height = Image.open(image).size
        id = p.id("page", artifact, number)
        p.add(
            "source_page",
            id=id,
            artifact_id=artifact,
            page_number=number,
            image_key=f"pages/{SOURCE}/{image.name}",
            width=width,
            height=height,
        )
        pages[stem, number] = id, "landscape" if width > height else "portrait"
        return pages[stem, number]

    def scanned(
        result_set: str, unit: str, option: str | None, cell: tuple[Decimal, mic_hoc_scans.Source], measure: int = 1
    ) -> None:
        amount, (stem, number, tile, _) = cell
        value(result_set, unit, option, amount, measure)
        id, orientation = page(stem, number)
        located[result_set, unit, option, measure] = id, mic_hoc_scans.TILES[orientation][tile]

    def accounting(result_set: str, unit: str, tier: str, number: int) -> None:
        (electors, voters), (cast, valid, invalid) = accounts[tier][0][number], accounts[tier][1][number]
        for measure, amount in (
            (REGISTERED, electors),
            (VOTERS, voters),
            (BALLOTS, cast),
            (VALID, valid),
            (INVALID, invalid),
        ):
            value(result_set, unit, None, amount, measure)

    def result_set(key: str, contest: str, default: bool, method: str) -> str:
        id = p.id("result_set", key)
        p.add(
            "result_set",
            id=id,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=default,
            completeness=1.0,
        )
        p.add("note", id=p.id("note", key), entity_id=id, kind="methodology", body_md=method, lang="en")
        return id

    election = p.id("election", held)
    p.add("election", id=election, polity_id=JAPAN, kind="general", date_from=HELD, date_to=HELD, status="held")
    p.name(election, "2007 Japanese House of Councillors election", "en", "official")
    p.name(election, "第21回参議院議員通常選挙", "ja", "native", False)
    path = f"jp/{held}-sangiin"
    p.add("slug", path=path, entity_id=election, is_current=True)
    p.add("note", id=p.id("note", election), entity_id=election, kind="caveat", body_md=COUNTING_NOTE, lang="en")

    english = dict(PREFECTURES)
    for number, candidates in sorted(facts.smd.items()):
        district = NAMES[number - 1]
        unit, area = prefecture(number)
        contest = p.id("contest", held, district)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="body_seats",
            body_id=house,
            district_area_id=area,
            magnitude=sum(candidate.elected for candidate in candidates),
            round_number=1,
            electoral_system_id=smd_system,
            default_result_kind="counted",
        )
        label = english[district]
        p.name(contest, f"{label} district", "en", "official")
        p.name(contest, f"{district}選挙区", "ja", "native", False)
        p.add("slug", path=f"{path}/{slug(label)}", entity_id=contest, is_current=True)
        counted = result_set(f"{held}:{district}", contest, True, SMD_METHOD)
        accounting(counted, unit, "smd", number)
        for candidate in candidates:
            option = p.id("option", held, district, candidate.order)
            member = party(candidate.party)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                party_id=member,
                ballot_label=candidate.name,
            )
            p.add(
                "candidacy",
                id=p.id("candidacy", held, district, candidate.order),
                person_id=person(f"smd:{district}:{plain(candidate.name)}", candidate.name),
                option_id=option,
                role="head",
                ballot_name=candidate.name,
                member_of_party_id=member,
                is_independent=member is None,
            )
            p.add(
                "outcome",
                result_set_id=counted,
                option_id=option,
                kind="elected" if candidate.elected else "lost",
                seats=1 if candidate.elected else None,
            )
            value(counted, unit, option, candidate.votes)
        if number in districts:
            finer = result_set(f"{held}:{district}:counting", contest, False, COUNTING_METHOD)
            for place in districts[number]:
                where = place_unit(number, place)
                for candidate in candidates:
                    option = p.id("option", held, district, candidate.order)
                    scanned(finer, where, option, place.cells[f"s{candidate.order}"])
                for key, measure in TURNOUT:
                    if key in place.cells:
                        scanned(finer, where, None, place.cells[key], measure)

    contest = p.id("contest", held, "national")
    p.add(
        "contest",
        id=contest,
        election_id=election,
        kind="list_allocation",
        body_id=house,
        district_area_id=nation_area,
        magnitude=SEATS[1],
        round_number=1,
        electoral_system_id=pr_system,
        default_result_kind="counted",
    )
    p.name(contest, "National proportional representation", "en", "official")
    p.name(contest, "比例代表選出", "ja", "native", False)
    p.add("slug", path=f"{path}/national", entity_id=contest, is_current=True)
    national = result_set(f"{held}:national", contest, True, PR_NATIONAL_METHOD)
    places = result_set(f"{held}:national:places", contest, False, PR_PLACE_METHOD)
    accounting(national, nation, "pr", 0)
    units = {number: prefecture(number)[0] for number in range(1, 48)}
    for number, unit in units.items():
        accounting(places, unit, "pr", number)
    for position, record in enumerate(records, 1):
        label = record["party"]
        short = mic_hoc_scans.party_name(label) or label
        member = party(label)
        listed = p.id("option", held, "national", label)
        p.add(
            "contest_option",
            id=listed,
            contest_id=contest,
            kind="list",
            party_id=member,
            ballot_position=position,
            ballot_label=party_label(label),
        )
        p.add(
            "outcome",
            result_set_id=national,
            option_id=listed,
            kind="won" if record["seats"] else "lost",
            seats=record["seats"],
        )
        legend = p.id("option", held, "national", label, "party")
        p.add("contest_option", id=legend, contest_id=contest, kind="legend", parent_option_id=listed, party_id=member)
        value(national, nation, legend, Decimal(record["party_votes"]))
        for number, unit in units.items():
            value(places, unit, legend, facts.totals[number][f"p:{short}"])
        for candidate in (row for row in people if row["party"] == label):
            name = candidate["name"]
            option = p.id("option", held, "national", label, plain(name))
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="candidate",
                parent_option_id=listed,
                party_id=member,
                ballot_label=name,
            )
            p.add(
                "candidacy",
                id=p.id("candidacy", held, "national", label, plain(name)),
                person_id=person(f"pr:{party_label(label)}:{plain(name)}", name),
                option_id=option,
                role="list_member",
                ballot_name=name,
                member_of_party_id=member,
                is_independent=False,
            )
            p.add(
                "outcome",
                result_set_id=national,
                option_id=option,
                kind="elected" if candidate["result"] == "当" else "lost",
            )
            value(national, nation, option, Decimal(candidate["votes"]))
            for number, unit in units.items():
                votes = facts.totals[number][f"c:{short}:{name}"]
                if votes:
                    value(places, unit, option, votes)

    for (result_set_id, unit, option, measure), amount in values.items():
        fact = {
            "result_set_id": result_set_id,
            "unit_id": unit,
            "option_id": option,
            "channel": 0,
            "measure": measure,
            "count_round": 0,
        }
        where = located.get((result_set_id, unit, option, measure))
        p.add("result_value", **fact, flags=0 if where is None else DIGITIZED, value=amount)
        if where is not None:
            (x0, y0, x1, y1) = where[1]
            p.add("value_locator", **fact, page_id=where[0], source_id=None, x0=x0, y0=y0, x1=x1, y1=y1)
    return p


def check(
    facts: mic_hoc_scans.Official,
    records: list[dict],
    people: list[dict],
    seats: dict[str, tuple[Decimal, int]],
    accounts: dict[str, tuple[dict[int, tuple[int, ...]], dict[int, tuple[int, ...]]]],
) -> None:
    if sorted(facts.smd) != list(range(1, 48)):
        raise ValueError(f"districts {sorted(facts.smd)}")
    won = 0
    for number, candidates in facts.smd.items():
        size = sum(candidate.elected for candidate in candidates)
        top = sorted(candidates, key=lambda candidate: -candidate.votes)[:size]
        if {c.order for c in top} != {c.order for c in candidates if c.elected}:
            raise ValueError(f"{NAMES[number - 1]}: the marked winners are not the top {size}")
        if not -1 < sum(c.votes for c in candidates) - facts.totals[number]["s:valid"] <= 0:
            raise ValueError(f"{NAMES[number - 1]}: candidates do not add up to the valid votes")
        won += size
    if (won, sum(record["seats"] for record in records)) != SEATS:
        raise ValueError(f"{won} district and {sum(r['seats'] for r in records)} list seats, expected {SEATS}")
    for tier, (turned, cast) in accounts.items():
        if sorted(turned) != list(range(48)) or sorted(cast) != list(range(48)):
            raise ValueError(f"{tier}: turnout rows {sorted(turned)}, ballot rows {sorted(cast)}")
        for figures in (turned, cast):
            for column in range(len(figures[0])):
                if sum(figures[number][column] for number in range(1, 48)) != figures[0][column]:
                    raise ValueError(f"{tier}: prefectures do not add up to the national row")
        for number in range(48):
            electors, voters = turned[number]
            total, valid, invalid = cast[number]
            if valid + invalid != total or total > voters or voters > electors:
                raise ValueError(f"{tier} {number}: ballots {cast[number]}, turnout {turned[number]}")
    totals = {record["party"]: Decimal(record["total"]) for record in records}
    if {name: won for name, (_, won) in seats.items()} != {r["party"]: r["seats"] for r in records}:
        raise ValueError("the seat table and the list table disagree")
    if dhondt(totals, SEATS[1]) != {r["party"]: r["seats"] for r in records}:
        raise ValueError("declared seats differ from D'Hondt on the party totals")
    for record in records:
        short = mic_hoc_scans.party_name(record["party"])
        listed = [person for person in people if person["party"] == record["party"]]
        names = [person["name"] for person in listed]
        if short is None or sorted(names) != sorted(facts.lists[short]) or len(names) != record["listed"]:
            raise ValueError(f"{record['party']}: the national list and the prefecture tables name different people")
        if sum(1 for person in listed if person["result"] == "当") != record["seats"]:
            raise ValueError(f"{record['party']}: elected members differ from seats")
        personal = sum((Decimal(person["votes"]) for person in listed), Decimal(0))
        if Decimal(record["total"]) - Decimal(record["party_votes"]) != personal:
            raise ValueError(f"{record['party']}: candidates sum {personal}")
        for part, letter in (("total", "t"), ("party_votes", "p"), ("candidate_votes", "l")):
            summed = sum((facts.totals[number][f"{letter}:{short}"] for number in range(1, 48)), Decimal(0))
            if summed != Decimal(record[part]):
                raise ValueError(f"{record['party']} {part}: prefectures {summed}, nation {record[part]}")
        for number in range(1, 48):
            here = facts.totals[number]
            listed_sum = sum((here[f"c:{short}:{person['name']}"] for person in listed), Decimal(0))
            if here[f"t:{short}"] - here[f"p:{short}"] != listed_sum or here[f"l:{short}"] != listed_sum:
                raise ValueError(f"{record['party']} {NAMES[number - 1]}: candidates {listed_sum}")
        for person in listed:
            summed = sum((facts.totals[n][f"c:{short}:{person['name']}"] for n in range(1, 48)), Decimal(0))
            if summed != Decimal(person["votes"]):
                raise ValueError(f"{record['party']} {person['name']}: prefectures {summed}, nation {person['votes']}")
