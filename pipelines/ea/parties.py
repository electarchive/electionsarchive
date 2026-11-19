import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from uuid import UUID

from ea.store import Connection

APART = ("list_vehicle", "independent_group", "nuance", "parliamentary_group", "caucus")
KEYS = ("official", "english", "native", "ballot", "historical", "transliteration")
GAP = 12
HUB = 3
GENERIC = re.compile(
    r"^(the )?(independ|indépend|unabh|bagimsiz|non ?partisan|unaffiliated|no party|none|others?|otros|outros|autres"
    r"|andere|sonstige|blank|write ?in|vacant|lista? (nr |no |n )?\d+|\d+)\b"
    r"|^(independent|independents|independiente|independientes|independente|independentes|indipendente|indipendenti"
    r"|nezavisni|niezależni|független|neodvisni|unabhängige?)$"
    r"|независим|nezávisl|nezavisl|independent candidates?|independent list|citizens? list|group of citizens"
    r"|grupa birača|grupa građana|wählergruppe|kandidatenliste|voters association"
)
LIVE = """select p.id::text, p.polity_id::text, p.kind::text, c.pipeline, coalesce(c.scope->>'package', c.pipeline),
                 coalesce((select array_agg(x.value) from external_id x
                           where x.entity_id = p.id and x.scheme = 'wikidata'), '{}'),
                 coalesce((select array_agg(n.text || chr(31) || n.lang || chr(31) || n.kind::text)
                           from name n where n.entity_id = p.id), '{}')
          from party p join changeset c on c.id = p.changeset_id join entity e on e.id = p.id
          where e.retired_by is null and p.polity_id is not null and p.kind::text <> all(%(apart)s)
            and not exists (select from entity_merge m where m.entity_id = p.id)
            and (%(polities)s::uuid[] is null or p.polity_id = any(%(polities)s::uuid[]))"""
FOOTPRINT = """select o.party_id::text, count(distinct k.election_id), count(*),
                      min(extract(year from e.date_from))::int, max(extract(year from e.date_from))::int,
                      coalesce(array_agg(distinct k.district_area_id::text)
                               filter (where k.district_area_id is not null), '{}')
               from contest_option o join contest k on k.id = o.contest_id join election e on e.id = k.election_id
               where o.party_id = any(%s) group by 1"""
PLACES = """select distinct on (entity_id) entity_id::text, text from name where entity_id = any(%s)
            order by entity_id, is_preferred desc, lang = 'en' desc"""


def fold(text: str) -> str:
    folded = unicodedata.normalize("NFKD", text.replace("ı", "i").replace("İ", "i"))
    plain = "".join(ch for ch in folded if not unicodedata.combining(ch)).casefold()
    return " ".join(re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", plain)))


def keyed(text: str) -> str | None:
    folded = fold(text)
    if len(folded.replace(" ", "")) < 4 or GENERIC.search(folded):
        return None
    return folded


@dataclass
class Party:
    id: str
    polity: str
    kind: str
    pipeline: str
    unit: str
    wikidata: set[str]
    names: set[str]
    native: set[str]
    elections: int = 0
    options: int = 0
    first: int | None = None
    last: int | None = None
    areas: set[str] = field(default_factory=set)
    national: bool = False


def party(
    id: str, polity: str, kind: str, pipeline: str, unit: str, wikidata: set[str], names: list[tuple[str, str, str]]
) -> Party:
    return Party(
        id,
        polity,
        kind,
        pipeline,
        unit,
        wikidata,
        {key for text, _, kind in names if kind in KEYS and (key := keyed(text))},
        {
            folded
            for text, lang, kind in names
            if lang not in ("en", "und") and kind != "abbreviation"
            if (folded := fold(text))
        },
    )


def live(conn: Connection, polities: list[str] | None = None) -> list[Party]:
    return [
        party(id, polity, kind, pipeline, unit, set(wikidata), [tuple(name.split("\x1f", 2)) for name in names])
        for id, polity, kind, pipeline, unit, wikidata, names in conn.execute(
            LIVE, {"apart": list(APART), "polities": polities}
        ).fetchall()
    ]


def place(conn: Connection, parties: list[Party]) -> None:
    by = {p.id: p for p in parties}
    areas: dict[str, list[str]] = {}
    for id, elections, options, first, last, districts in conn.execute(FOOTPRINT, [[UUID(id) for id in by]]).fetchall():
        p = by[id]
        p.elections, p.options, p.first, p.last = elections, options, first, last
        areas[id] = districts
    names = dict(conn.execute(PLACES, [[UUID(a) for a in {a for found in areas.values() for a in found}]]).fetchall())
    countries: dict[str, set[str]] = defaultdict(set)
    for polity, text in conn.execute(
        "select entity_id::text, text from name where entity_id = any(%s)", [[UUID(p.polity) for p in parties]]
    ).fetchall():
        countries[polity].add(fold(text))
    for id, found in areas.items():
        p = by[id]
        p.areas = {fold(names[a]) for a in found if a in names}
        p.national = bool(p.areas & countries[p.polity]) or p.pipeline.startswith("eu.")


def groups(parties: list[Party]) -> list[list[Party]]:
    parent: dict[str, str] = {}

    def root(x: str) -> str:
        while parent.setdefault(x, x) != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    keys: dict[tuple[str, str], list[str]] = defaultdict(list)
    for p in parties:
        for name in p.names:
            keys[(p.polity, name)].append(p.id)
    for ids in keys.values():
        for id in ids[1:]:
            parent[root(id)] = root(ids[0])
    by = {p.id: p for p in parties}
    found: dict[str, list[Party]] = defaultdict(list)
    for id in parent:
        found[root(id)].append(by[id])
    return [members for members in found.values() if len({p.unit for p in members}) > 1]


def judge(
    members: list[Party],
    spans: dict[str, tuple[int | None, int | None]],
    related: set[frozenset[str]],
    apart: set[frozenset[str]],
) -> tuple[str, str]:
    items = {item for p in members for item in p.wikidata}
    if len(items) > 1:
        return "apart", "different Wikidata items: " + ", ".join(sorted(items))
    pairs = [(a, b) for a in members for b in members if a.id < b.id]
    for a, b in pairs:
        if frozenset((a.id, b.id)) in related:
            return "apart", "a party relation links two of them (a refounding, merger, or split)"
        if frozenset((a.id, b.id)) in apart:
            return "apart", "kept apart by hand in party_apart.tsv"
        if a.native and b.native and not a.native & b.native:
            return "apart", "their names in the native script differ"
    kinds = {p.kind for p in members}
    if len(kinds) > 1:
        return "review", "their kinds differ: " + ", ".join(sorted(kinds))
    if any(not a.names & b.names for a, b in pairs):
        return "review", "chained through a third record; two of them share no name"
    twice = sorted(unit for unit, n in Counter(p.unit for p in members).items() if n > 1)
    if twice:
        return "review", "one source holds several of them: " + ", ".join(twice)
    placed = [p for p in members if p.areas]
    reached = {placed[0].id} if placed else set()
    stack = placed[:1]
    while stack:
        x = stack.pop()
        for y in placed:
            if y.id not in reached and (
                x.areas & y.areas or (x.national or y.national) and max(len(x.areas), len(y.areas)) >= HUB
            ):
                reached.add(y.id)
                stack.append(y)
    if len(reached) < len(placed):
        return "review", "only local, in places that do not meet"
    item = next(iter(items), None)
    began, ended = spans.get(item, (None, None)) if item else (None, None)
    if began is not None or ended is not None:
        outside = [
            p
            for p in members
            if p.first is not None
            and p.last is not None
            and ((began is not None and p.first < began - 1) or (ended is not None and p.last > ended + 1))
        ]
        if outside:
            return "review", f"active outside {item}'s years ({began}-{ended})"
    else:
        dated = sorted((p.first, p.last) for p in members if p.first is not None and p.last is not None)
        end = None
        for first, last in dated:
            if end is not None and first - end > GAP:
                return "review", f"{first - end} years between records and no Wikidata dates"
            end = last if end is None else max(end, last)
    if not items and all(len(name.replace(" ", "")) <= 4 for name in set.intersection(*(p.names for p in members))):
        return "review", "only a short name in common and no Wikidata item"
    return "merge", ""


def survivor(members: list[Party]) -> Party:
    return min(members, key=lambda p: (not p.wikidata, -p.elections, -p.options, p.id))


class Resolver:
    """Finds the live party a new record names, by Wikidata id or by an exact full name in the same polity."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn
        self.polities: dict[str, tuple[dict[str, Party], dict[str, list[Party]]]] = {}

    def _index(self, polity: str) -> tuple[dict[str, Party], dict[str, list[Party]]]:
        if polity not in self.polities:
            parties = live(self.conn, [polity])
            items = {item: p for p in parties for item in p.wikidata}
            names: dict[str, list[Party]] = defaultdict(list)
            for p in parties:
                for name in p.names:
                    names[name].append(p)
            self.polities[polity] = items, names
        return self.polities[polity]

    def find(
        self, polity: str, kind: str, names: list[tuple[str, str, str]], wikidata: str | None = None
    ) -> Party | None:
        items, index = self._index(polity)
        if wikidata is not None and wikidata in items:
            return items[wikidata]
        if kind in APART:
            return None
        new = party("", polity, kind, "", "", set(), names)
        found = {
            p.id: p
            for name in new.names
            for p in index.get(name, [])
            if p.kind == kind
            and not (wikidata is not None and p.wikidata)
            and not (new.native and p.native and not new.native & p.native)
        }
        if len(found) != 1:
            return None
        match = next(iter(found.values()))
        if not match.wikidata:
            place(self.conn, [match])
            if not match.national:
                return None
        return match
