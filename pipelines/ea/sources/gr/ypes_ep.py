"""Greece's European Parliament elections of 2019 and 2024 by polling station, from the Ministry of Interior's results site.

The country is one constituency: 21 seats are shared among the parties in proportion to their votes. The Ministry's
results service publishes one small data file for each polling station (`tm_<id>.js`, a `tmima`), one for each of the
electoral districts the votes are counted in (`ep_<n>.js`, 57 of them with the overseas count), and one for the nation
(`epik_1.js`). Files saved from a browser put many of them under `<!-- path -->` headers, and `split` takes them apart.

- **2024** has the three kinds of files, and a static file for each polling station with its name and kind, so every
  polling station, postal-vote counting station, and prison station is a reporting unit. The stations add up to the
  Ministry's national file.
- **2019** has the stations' counts but not their names or kinds. The service has no station files for three districts
  (7, 63, and 64) or for 34 of the 1,309 stations of district 62. For the three districts the finest unit published is
  the municipal unit (`den_<n>.js`), so each municipal unit is an aggregate unit under its district. What the district's
  file holds beyond its units (one counting station of district 7 and one of district 64, which the municipal units
  omit) and beyond its stations (the 34 of district 62, which the files do not tie to a municipal unit) is stored as one
  aggregate unit for the district, computed as the difference, so the units add up to the Ministry's district figures.
  The 2019 national figures are the previous-election block of the 2024 national static file, which the Ministry
  prints. The 2019 party names are those of the 2019 site's static file.

The 2009 and 2014 elections are built by `ypes_national` and `ypes_results` at the unit those years publish, and the
1999 and 2004 ones by `ypes_results` as national results; earlier elections are not on the Ministry's site.

Each election is a package, `gr.ypes_ep/ep-<year>`, built on `gr.ypes_ep/geo` (the schemes) and `gr.ypes_ep/registry`
(the source and the parties).
"""

import json
import os
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "gr.ypes_ep"
SITE = "https://ekloges.ypes.gr/"
GREECE = str(uuid5(package.NAMESPACE, "polity\x1fGR"))
TERRITORY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fGRC"))
GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"
DERIVED = 1
ABROAD = 57
SEATS = 21
STATION = "gr.eklogiko_tmima"
DISTRICT = "gr.ep_district"
MEASURES = (2, 3, 4, 5, 6)  # registered, cast, valid, invalid, blank
KINDS = {"Κ": "standard", "Τ": "postal_district", "Φ": "prison"}

OLD = "https://ekloges-prev.singularlogic.eu/"
LIVE_STATIONS = f"{SITE}current/stat/e/ep_{{EP}}.js"
LIVE_NATIONAL = f"{SITE}current/stat/e/epik_1.js"
OLD_STATIONS = f"{OLD}2019/stat/e/ep_{{EP}}.js"
OLD_EXTRA = (f"{OLD}2019/dyn/e/40/tm_405100.js", f"{OLD}2019/dyn/e/60/tm_600944.js")
STATICS_2014 = f"{OLD}may2014/stat/e/statics.js"
STATICS_2019 = f"{OLD}2019/stat/e/statics.js"
UNITS_2019 = "browser://20261118-192640-d46ed1/gr-2019-euro-municipal-units-bundle.txt"
UNIT = "gr.ep_municipal_unit"


@dataclass(frozen=True)
class Election:
    key: str
    day: date
    stations: tuple[str, ...]
    prefix: str  # the folder of the site's files inside the bundles
    names: tuple[str, ...] = ()  # files that name parties, newest last
    units: str | None = None  # the file of municipal-unit counts, for districts that have no station files


ELECTIONS = [
    Election(
        "ep-2019",
        date(2019, 5, 26),
        (OLD_STATIONS, *OLD_EXTRA),
        "2019",
        (STATICS_2014, STATICS_2019, LIVE_NATIONAL),
        UNITS_2019,
    ),
    Election("ep-2024", date(2024, 6, 9), (LIVE_STATIONS, LIVE_NATIONAL), "current", (LIVE_NATIONAL,)),
]
BY_KEY = {e.key: e for e in ELECTIONS}
FILES = {STATICS_2014, STATICS_2019, UNITS_2019, LIVE_STATIONS, LIVE_NATIONAL, OLD_STATIONS, *OLD_EXTRA}


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


YPES = key("source", "ypes")


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/{e.key}" for e in ELECTIONS)]


def fetch() -> None:
    """Store the files saved from a browser, listed in `index.json` of the folder named by `EA_GR_FETCHED`."""
    folder = Path(os.environ["EA_GR_FETCHED"])
    index = json.loads((folder / "index.json").read_text())
    for url, entry in index.items():
        if url not in FILES:
            continue
        raw.store(
            SOURCE,
            entry.get("source", url),
            (folder / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]).astimezone(UTC),
            media_type=entry["media_type"],
        )


def parse() -> None:
    geo().write()
    registry().write()
    for e in ELECTIONS:
        election(e).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------- reading


def split(text: str) -> dict[str, str]:
    parts = re.split(r"^<!-- (.+?) -->\n", text, flags=re.MULTILINE)
    return dict(zip(parts[1::2], parts[2::2], strict=True))


def value(text: str) -> object:
    return json.JSONDecoder().raw_decode(text.strip())[0]


def mapping(item: object) -> dict[str, object]:
    if not isinstance(item, dict):
        raise TypeError(f"expected an object, found {type(item).__name__}")
    return item


def listing(item: object) -> list[object]:
    if not isinstance(item, list):
        raise TypeError(f"expected a list, found {type(item).__name__}")
    return item


def whole(item: object) -> int:
    if not isinstance(item, int | str):
        raise TypeError(f"expected a number, found {item!r}")
    return int(item)


_SEGMENTS: dict[str, dict[str, str]] = {}


def segments(url: str) -> dict[str, str]:
    if url not in _SEGMENTS:
        _SEGMENTS[url] = split(raw.cached(SOURCE, url).path.read_text(encoding="utf-8"))
    return _SEGMENTS[url]


@dataclass
class Figures:
    registered: int
    valid: int
    invalid: int
    blank: int
    votes: dict[int, int]
    seats: dict[int, int]
    cast: int

    def minus(self, parts: list[Figures]) -> Figures:
        votes = {pid: n - sum(p.votes.get(pid, 0) for p in parts) for pid, n in self.votes.items()}
        valid = self.valid - sum(p.valid for p in parts)
        invalid = self.invalid - sum(p.invalid for p in parts)
        blank = self.blank - sum(p.blank for p in parts)
        return Figures(
            self.registered - sum(p.registered for p in parts),
            valid,
            invalid,
            blank,
            votes,
            {},
            valid + invalid + blank,
        )

    @property
    def empty(self) -> bool:
        return not (self.registered or self.valid or self.invalid or self.blank or any(self.votes.values()))


def figures(row: dict[str, object], party: object, seats: str | None = None) -> Figures:
    votes: dict[int, int] = {}
    won: dict[int, int] = {}
    for entry in listing(party):
        got = mapping(entry)
        votes[whole(got["PARTY_ID"])] = whole(got["VOTES"])
        if seats is not None and got.get(seats):
            won[whole(got["PARTY_ID"])] = whole(got[seats])
    valid, invalid, blank = whole(row["Egkyra"]), whole(row["Akyra"]), whole(row["Leyka"])
    cast = whole(row["TotalVotes"]) if row.get("TotalVotes") is not None else valid + invalid + blank
    if cast != valid + invalid + blank:
        raise ValueError(f"a count of {valid} valid, {invalid} invalid, and {blank} blank ballots casts {cast}")
    if sum(votes.values()) != valid:
        raise ValueError(f"the parties' votes add up to {sum(votes.values())}, and valid ballots to {valid}")
    return Figures(whole(row["Gramenoi"]), valid, invalid, blank, votes, won, cast)


@dataclass
class Station:
    id: int
    district: int
    name: str | None
    kind: str
    figures: Figures


@dataclass
class Unit:
    id: int
    district: int
    name: str
    figures: Figures


@dataclass
class Data:
    stations: list[Station]
    units: list[Unit]
    districts: dict[int, Figures]
    district_names: dict[int, str]
    national: Figures
    declared: dict[str, int]  # the measures the Ministry's national file gives
    updated: str


def national(e: Election) -> tuple[Figures, dict[str, int]]:
    """The Ministry's national totals: its own file for 2024, and for 2019 the previous-election block printed in it."""
    live = segments(LIVE_NATIONAL)
    if e.key == "ep-2024":
        row = mapping(value(live["/current/dyn1/e/epik_1.js"]))
        if not row.get("official"):
            raise ValueError("the national file is not the official count")
        found = figures(row, row["party"], "EdresEpik")
        return found, {
            "registered": found.registered,
            "valid": found.valid,
            "invalid": found.invalid,
            "blank": found.blank,
        }
    row = mapping(value(live["/current/stat/e/epik_1.js"]))
    previous = mapping(row["prev"])
    votes = {int(pid): whole(mapping(got)["PVOTES"]) for pid, got in previous.items()}
    seats = {int(pid): whole(mapping(got)["PEdresEpik"]) for pid, got in previous.items() if mapping(got)["PEdresEpik"]}
    valid = whole(row["PEgkyra"])
    if sum(votes.values()) != valid:
        raise ValueError(
            f"the previous election's votes add up to {sum(votes.values())}, and its valid ballots to {valid}"
        )
    return Figures(0, valid, 0, 0, votes, seats, valid), {"valid": valid}


def load(e: Election) -> Data:
    dyn: dict[int, tuple[int, Figures]] = {}
    stat: dict[int, dict[str, object]] = {}
    districts: dict[int, Figures] = {}
    names: dict[int, str] = {}
    updated = ""
    for url in e.stations:
        for path, text in segments(url).items():
            found = re.search(r"/(dyn|stat)/e/(?:(\d+)/tm_(\d+)|ep_(\d+))\.js$", path)
            if found is None or not path.startswith(f"/{e.prefix}/"):
                continue
            body = value(text)
            if body is None:
                continue  # a file for a number the service never used
            row = mapping(body)
            if found.group(4) is not None:
                n = int(found.group(4))
                if found.group(1) == "stat":
                    names[n] = str(row["Descr"])
                else:
                    districts[n] = figures(row, row["party"])
                    updated = max(updated, str(row["Updated"]))
            elif found.group(1) == "stat":
                stat[int(found.group(3))] = row
            else:
                dyn[int(found.group(3))] = (int(found.group(2)), figures(row, row["party"]))
    stations = []
    for tm in sorted(dyn):
        n, got = dyn[tm]
        row = stat.get(tm)
        stations.append(
            Station(tm, n, str(row["Descr"]) if row else None, KINDS[str(row["TM_KIND"])] if row else "standard", got)
        )
    if set(stat) - set(dyn):
        raise ValueError(f"{len(set(stat) - set(dyn))} stations have a static file and no count")
    total, declared = national(e)
    return Data(
        stations, municipal_units(e, {s.district for s in stations}), districts, names, total, declared, updated
    )


def municipal_units(e: Election, stationed: set[int]) -> list[Unit]:
    """The municipal units of the districts that have no station files, from the municipal units' own files."""
    if e.units is None:
        return []
    stat: dict[int, dict[str, object]] = {}
    dyn: dict[int, dict[str, object]] = {}
    for path, text in segments(e.units).items():
        found = re.search(rf"/{e.prefix}/(dyn|stat)/e/den_(\d+)\.js$", path)
        if found is None:
            continue
        body = value(text)
        if body is not None:
            (stat if found.group(1) == "stat" else dyn)[int(found.group(2))] = mapping(body)
    if set(stat) != set(dyn):
        raise ValueError(f"{len(set(stat) ^ set(dyn))} municipal units have only one of their two files")
    units = []
    for n in sorted(stat):
        row = stat[n]
        district = whole(row["EP_ID"])
        if district not in stationed:
            units.append(Unit(n, district, str(row["Descr"]), figures(dyn[n], dyn[n]["party"])))
    return units


_DATA: dict[str, Data] = {}


def data(e: Election) -> Data:
    if e.key not in _DATA:
        _DATA[e.key] = load(e)
        _verify(e, _DATA[e.key])
    return _DATA[e.key]


def leaves(e: Election) -> tuple[list[Station], dict[int, Figures]]:
    """The stations, and the figures of each district that its stations do not hold."""
    d = data(e)
    held: dict[int, list[Figures]] = defaultdict(list)
    for station in d.stations:
        held[station.district].append(station.figures)
    for unit in d.units:
        held[unit.district].append(unit.figures)
    rest = {n: whole_.minus(held[n]) for n, whole_ in sorted(d.districts.items())}
    return d.stations, {n: f for n, f in rest.items() if not f.empty}


def _verify(e: Election, d: Data) -> None:
    """Everything counted must add up to the Ministry's national figures."""
    stations, rest = leaves(e)
    parts = [s.figures for s in stations] + [u.figures for u in d.units] + list(rest.values())
    for n, f in rest.items():
        if min(f.registered, f.valid, f.invalid, f.blank, *f.votes.values()) < 0:
            raise ValueError(f"{e.key}: district {n}'s stations hold more than the district")
    if e.key == "ep-2024" and rest:
        raise ValueError(f"{e.key}: districts {sorted(rest)} differ from their stations")
    for pid in d.national.votes:
        got = sum(p.votes.get(pid, 0) for p in parts)
        if got != d.national.votes[pid]:
            raise ValueError(
                f"{e.key}: party {pid} has {got} votes in the units and {d.national.votes[pid]} nationally"
            )
    extra = {pid for p in parts for pid in p.votes} - set(d.national.votes)
    if any(sum(p.votes.get(pid, 0) for p in parts) for pid in extra):
        raise ValueError(f"{e.key}: votes for parties the national file lacks")
    for name, count in d.declared.items():
        if count != sum(getattr(p, name) for p in parts):
            raise ValueError(f"{e.key}: units hold {sum(getattr(p, name) for p in parts)} {name}, not {count}")
    if sum(d.national.seats.values()) != SEATS:
        raise ValueError(f"{e.key}: {sum(d.national.seats.values())} seats were allocated")


# ---------------------------------------------------------------- parties


def model_names(text: str) -> dict[int, str]:
    return {
        int(found.group(1)): (found.group(2) or found.group(3)).strip()
        for found in re.finditer(r"""[{,](\d+):\{id:\d+,name:(?:"([^"]+)"|'([^']+)'),shortName:""", text)
    }


def statics_names(text: str) -> dict[int, str]:
    start = text.index("party:[") + len("party:")
    depth = 0
    for i in range(start, len(text)):
        depth += {"[": 1, "]": -1}.get(text[i], 0)
        if depth == 0:
            return {
                whole(row[0]): re.sub(r"\s+", " ", str(row[2])).strip()
                for row in map(listing, listing(json.loads(text[start : i + 1])))
            }
    raise ValueError("unbalanced party list")


def party_names() -> dict[int, dict[str, date]]:
    """Each party number's spellings, with the day of the newest election whose site gave it."""
    found: dict[int, dict[str, date]] = defaultdict(dict)
    sources = [
        (statics_names(segments(STATICS_2014)["/may2014/stat/e/statics.js"]), date(2014, 5, 25)),
        (statics_names(raw.cached(SOURCE, STATICS_2019).path.read_text(encoding="utf-8")), date(2019, 5, 26)),
        (model_names(segments(LIVE_NATIONAL)["/current/e/home/dist/model.el.js"]), date(2024, 6, 9)),
    ]
    for names, day in sources:
        for pid, text in names.items():
            found[pid][text] = max(day, found[pid].get(text, date.min))
    return found


def party(pid: int) -> str:
    return key("party", pid)


def label(pid: int, e: Election) -> str | None:
    """The party's name on the election's own site, or else its newest spelling."""
    spellings = party_names().get(pid)
    if not spellings:
        return None
    own = {text: day for text, day in spellings.items() if day == e.day}
    return max(own or spellings, key=lambda text: (own or spellings)[text])


# ---------------------------------------------------------------- packages


def cited(p: Package, source: bool = False) -> None:
    for url in sorted(FILES):
        artifact = raw.cached(SOURCE, url)
        if source:
            p.artifact(artifact, YPES)
        else:
            p.uses(artifact)


def geo() -> Package:
    p = Package(
        GEO, SOURCE, "The reporting levels of Greece's European Parliament results: districts and polling stations"
    )
    p.external |= {GREECE, TERRITORY}
    p.add("scheme", code=DISTRICT, polity_id=GREECE, kind="electoral_district", rank=1, noun="counting district")
    p.add("scheme", code=UNIT, polity_id=GREECE, kind="administrative", rank=2, noun="municipal unit")
    p.add("scheme", code=STATION, polity_id=GREECE, kind="precinct", rank=3, noun="polling station")
    return p


def used() -> set[int]:
    return {pid for e in ELECTIONS for pid in data(e).national.votes} | {
        pid for e in ELECTIONS for f in data(e).districts.values() for pid in f.votes
    }


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Greek parties and the Ministry of Interior's results service, for the European Parliament results by polling station",
        depends=[GEO],
    )
    p.external.add(GREECE)
    p.add(
        "source",
        id=YPES,
        kind="official_body",
        publisher="Υπουργείο Εσωτερικών (Ministry of Interior)",
        title="Εκλογές: αποτελέσματα (election results)",
        url=SITE,
        license="Official publication; reuse terms are not stated",
        license_url=SITE,
        attribution="Υπουργείο Εσωτερικών",
    )
    cited(p, True)
    names = party_names()
    for pid in sorted(used()):
        spellings = names.get(pid)
        if not spellings:
            continue
        id = party(pid)
        p.add("party", id=id, kind="party", polity_id=GREECE)
        p.add("external_id", entity_id=id, scheme="gr.ypes_ep.party", value=str(pid))
        newest = max(spellings, key=lambda text: spellings[text])
        for text in sorted(spellings):
            p.name(id, text, "el", "official" if text == newest else "ballot", text == newest)
    return p


METHOD = (
    "The Ministry of Interior's results service counts each polling station and publishes its registered voters, "
    "ballots, and votes for each party, and the same for each district the votes are counted in and for the nation. "
    "The values here are the polling stations' own figures. Seats are shared nationally, by the largest-remainder "
    "method among the parties above the 3% threshold. Postal-vote counting stations and prison stations are counted "
    "as stations of their own."
)
RESIDUAL = (
    "The Ministry's service has no station files for some districts of this election, and its district files give "
    "those districts' figures. For a district with no station files, the counts of its municipal units are stored as "
    "aggregate units. Whatever a district's file holds beyond its units and stations, such as one counting station that "
    "the municipal units omit or 34 stations of one Athens district that the files do not tie to a municipal unit, is "
    "stored as one aggregate unit for the district, computed as the district's figures less its units and stations, so "
    "that the units add up to the districts. The Ministry's 2019 national totals are the previous-election block "
    "printed in its 2024 national file: valid ballots, votes for each party, and seats. The other national figures of "
    "2019 are not declared."
)


def election(e: Election) -> Package:
    d = data(e)
    stations, rest = leaves(e)
    p = Package(
        f"{SOURCE}/{e.key}",
        SOURCE,
        f"The {e.day.year} European Parliament election in Greece by polling station, from the Ministry of Interior's results site",
        depends=[GEO, REGISTRY],
    )
    p.external |= {GREECE, TERRITORY}
    cited(p)
    id = key("election", e.key)
    path = f"gr/{e.day.isoformat()}-european-parliament"
    p.add(
        "election",
        id=id,
        polity_id=GREECE,
        kind="general",
        date_from=e.day,
        date_to=e.day,
        date_precision="day",
        status="held",
    )
    p.name(id, f"{e.day.year} European Parliament election in Greece", "en", "official")
    p.name(id, f"Ευρωεκλογές {e.day.year}", "el", "native", False)
    p.add("slug", path=path, entity_id=id, is_current=True)

    title = "Greek seats in the European Parliament"
    contest = key("contest", e.key, "national")
    p.add(
        "contest",
        id=contest,
        election_id=id,
        kind="list_allocation",
        body_id=None,
        district_area_id=TERRITORY,
        magnitude=SEATS,
        round_number=1,
        default_result_kind="counted",
    )
    p.name(contest, title, "en", "official")
    p.name(contest, "Έδρες Ελλάδας στο Ευρωπαϊκό Κοινοβούλιο", "el", "native", False)
    p.add("slug", path=f"{path}/{changeset.slug(title)}", entity_id=contest, is_current=True)
    rs = key("result_set", e.key, "national")
    p.add(
        "result_set",
        id=rs,
        election_id=id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=YPES,
        status="active",
        is_default=True,
        completeness=1.0,
    )
    p.add("note", id=key("note", rs), entity_id=rs, kind="methodology", body_md=METHOD, lang="en")
    if rest or d.units:
        p.add("note", id=key("note", rs, "residual"), entity_id=rs, kind="caveat", body_md=RESIDUAL, lang="en")

    pids = sorted(d.national.votes, key=lambda pid: -d.national.votes[pid])
    options: dict[int, str] = {}
    for pid in pids:
        options[pid] = key("option", e.key, pid)
        name = label(pid, e)
        p.add(
            "contest_option",
            id=options[pid],
            contest_id=contest,
            kind="list",
            party_id=party(pid) if name else None,
            ballot_label=name or f"Party {pid}",
        )

    def unit(scheme: str, code: str, kind: str, district: int | None, parent: str | None, name: str | None) -> str:
        uid = key("unit", e.key, scheme, code)
        abroad = district == ABROAD
        p.add(
            "reporting_unit",
            id=uid,
            polity_id=GREECE,
            scheme_code=scheme,
            code=f"{e.day}:{code}",
            parent_id=parent,
            area_relation="unlocated" if abroad else "within",
            area_id=None if abroad else TERRITORY,
            kind=kind,
        )
        if name:
            p.name(uid, re.sub(r"\s+", " ", name).strip(), "el", "official")
        return uid

    parents = {
        n: unit(DISTRICT, str(n), "aggregate", n, None, d.district_names.get(n))
        for n in sorted({s.district for s in stations} | {u.district for u in d.units} | set(rest))
    }
    rows: dict[str, list[object]] = {c: [] for c in ("unit_id", "option_id", "measure", "flags", "value")}

    def emit(uid: str, f: Figures, flags: int = 0) -> None:
        for measure, count in zip(MEASURES, (f.registered, f.cast, f.valid, f.invalid, f.blank), strict=True):
            if count > 0 or (count == 0 and not flags):
                for column, item in zip(rows, (uid, None, measure, flags, count), strict=True):
                    rows[column].append(item)
        for pid, count in f.votes.items():
            if count > 0 or (count == 0 and not flags):
                for column, item in zip(rows, (uid, options[pid], 1, flags, count), strict=True):
                    rows[column].append(item)

    for s in stations:
        uid = unit(
            STATION,
            str(s.id),
            "postal_district" if s.kind == "postal_district" and s.district != ABROAD else s.kind,
            s.district,
            parents[s.district],
            s.name,
        )
        emit(uid, s.figures)
    for u in d.units:
        emit(unit(UNIT, str(u.id), "aggregate", u.district, parents[u.district], u.name), u.figures)
    for n, f in rest.items():
        emit(unit(STATION, f"residual:{n}", "aggregate", n, parents[n], None), f, DERIVED)
    frame = pl.DataFrame(
        rows,
        schema={
            "unit_id": pl.String,
            "option_id": pl.String,
            "measure": pl.Int16,
            "flags": pl.Int16,
            "value": pl.Int64,
        },
    )
    p.frame(
        "result_value",
        frame.with_columns(result_set_id=pl.lit(rs), channel=pl.lit(0, pl.Int16), count_round=pl.lit(0, pl.Int16)),
    )
    for name, measure in (("registered", 2), ("valid", 4), ("invalid", 5), ("blank", 6)):
        if name in d.declared:
            p.add("declared", result_set_id=rs, option_id=None, measure=measure, value=d.declared[name])
    for pid, count in d.national.votes.items():
        p.add("declared", result_set_id=rs, option_id=options[pid], measure=1, value=count)
    for pid, count in sorted(d.national.seats.items()):
        p.add("outcome", result_set_id=rs, option_id=options[pid], kind="won", seats=count)
    if e.key == "ep-2019":
        p.accept(
            "accounting.turnout",
            "The Ministry's counts for stations 601550 and 623632 give one ballot more than registered voters "
            "(310 cast against 309 registered, and 335 against 334).",
        )
    return p
