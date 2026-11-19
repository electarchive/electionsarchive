"""Panama's party primaries that the Tribunal Electoral ran: the 2018 primaries of Cambio Democrático, the PRD, Alianza,
the Panameñista Party, and the Frente Amplio por la Democracia, and the 2023 primary of Realizando Metas, from the
spreadsheets the Tribunal posts for each."""

import json
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package
from ea.sources.pa import te
from ea.store import DATA

SOURCE = "pa.te_primarias"
SITE = te.SITE
INBOX = DATA / "inbox" / SOURCE
UPLOADS = SITE + "/wp-content/uploads/"
PAGE = SITE + "/eventos-electorales/elecciones-primarias/"

VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = te.VOTES, te.REGISTERED, te.CAST, te.VALID, te.INVALID, te.BLANK
PANAMA, COUNTRY = te.PANAMA, te.COUNTRY
CIRCUIT, DISTRICT, CORREGIMIENTO = "pa.circuito", "pa.distrito", "pa.corregimiento"
NATION, MESA = "pa.nacion", "pa.mesa"

GEO = f"{SOURCE}/geo"
REGISTRY = f"{SOURCE}/registry"


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SYSTEM = key("electoral_system", "plurality")
NATIONAL_AREA = COUNTRY


def fetch() -> None:
    """Load the files saved from the Tribunal's site (data/inbox/pa.te_primarias/) into the raw cache."""
    index = json.loads((INBOX / "index.json").read_text())
    for name, entry in index.items():
        if not entry["file"].endswith(".xlsx"):
            continue
        raw.store(
            SOURCE,
            entry.get("source", name),
            (INBOX / entry["file"]).read_bytes(),
            suffix=Path(entry["file"]).suffix,
            retrieved_at=datetime.fromisoformat(entry["retrieved_at"]),
            media_type=entry["media_type"],
        )


# Events ---------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Party:
    code: str  # the code of the party in the archive's Panama registry
    name: str  # as the Tribunal's pages name it
    slug: str


CD = Party("CD", "Cambio Democrático", "cd")
PRD = Party("PRD", "Partido Revolucionario Democrático", "prd")
ALIANZA = Party("ALIANZA", "Partido Alianza", "alianza")
PAN = Party("PAN", "Partido Panameñista", "pan")
FAD = Party("FAD", "Frente Amplio por la Democracia", "fad")
RM = Party("RM", "Realizando Metas", "rm")

# The files, by the name the Tribunal gives them.
F = {
    "cd-mesa": UPLOADS + "2018/10/RESULTADOS-CD-PRIMARIAS-TER-POR-MESA-1.xlsx",
    "prd-president": UPLOADS + "2018/10/PRESIDENTE-PRD-EXTRAOFICIAL-1.xlsx",
    "prd-deputy": UPLOADS + "2018/10/DIPUTADOS-PRD-EXTRAOFICIAL-1.xlsx",
    "prd-mayor": UPLOADS + "2018/10/ALCALDES-PRD-EXTRAOFICIAL-1.xlsx",
    "prd-rep": UPLOADS + "2018/10/REPRESENTANTES-PRD-EXTRAOFICIAL-1.xlsx",
    "prd-council": UPLOADS + "2018/10/CONCEJALES-PRD-EXTRAOFICIAL-1.xlsx",
    "alianza-president": UPLOADS + "2018/11/PRESIDENTE-ALIANZA.xlsx",
    "pan-president": UPLOADS + "2018/11/PRESIDENTE-PAN.xlsx",
    "pan-deputy": UPLOADS + "2018/11/DIPUTADOS-PAN.xlsx",
    "pan-mayor": UPLOADS + "2018/11/ALCALDES-PAN.xlsx",
    "pan-rep": UPLOADS + "2018/11/REPRESENTANTES-PAN.xlsx",
    "fad-president": UPLOADS + "2018/11/PRESIDENTE-FAD-2.xlsx",
    "rm-1": UPLOADS + "2023/07/Cuadro-01-Presidente-Primarias-RM-2023.xlsx",
    "rm-2": UPLOADS + "2023/07/Cuadro-02-Presidente-Cand-Primarias-RM-2023.xlsx",
}


@dataclass(frozen=True)
class Event:
    day: date
    parties: tuple[Party, ...]
    files: tuple[str, ...]
    stage: str
    english: str
    spanish: str


EVENTS = [
    Event(
        date(2018, 8, 12), (CD,), ("cd-mesa",), "preliminary",
        "2018 Cambio Democrático presidential primary", "Elecciones primarias presidenciales de Cambio Democrático, 2018",
    ),
    Event(
        date(2018, 9, 16), (PRD,), ("prd-president", "prd-deputy", "prd-mayor", "prd-rep", "prd-council"), "preliminary",
        "2018 PRD primaries", "Elecciones primarias del PRD, 2018",
    ),
    Event(
        date(2018, 10, 7), (ALIANZA,), ("alianza-president",), "preliminary",
        "2018 Partido Alianza presidential primary", "Elecciones primarias presidenciales del Partido Alianza, 2018",
    ),
    Event(
        date(2018, 10, 28), (PAN, FAD), ("pan-president", "pan-deputy", "pan-mayor", "pan-rep", "fad-president"),
        "preliminary",
        "2018 Panameñista Party and FAD primaries", "Elecciones primarias del Partido Panameñista y del FAD, 2018",
    ),
    Event(
        date(2023, 6, 4), (RM,), ("rm-1", "rm-2"), "official",
        "2023 Realizando Metas presidential primary", "Elecciones primarias presidenciales de Realizando Metas, 2023",
    ),
]  # fmt: skip


# Reading ------------------------------------------------------------------------------------------------------------


def frame(name: str) -> pl.DataFrame:
    """A table of the 2018 files: a header row, then one row per figure."""
    return pl.read_excel(raw.cached(SOURCE, F[name]).path, engine="calamine", infer_schema_length=None)


def grid(name: str) -> list[list[object]]:
    data = pl.read_excel(raw.cached(SOURCE, F[name]).path, engine="calamine", has_header=False, infer_schema_length=0)
    return [list(row) for row in data.iter_rows()]


def words(text: object) -> str:
    return " ".join(str(text).split())


def person_name(text: object) -> str:
    return te.pretty(words(text))


def number(cell: object) -> float | None:
    if cell is None or isinstance(cell, bool):
        return None
    try:
        return float(str(cell).replace(",", ""))
    except ValueError:
        return None


def numbers(cells: Sequence[object]) -> list[float]:
    return [n for n in map(number, cells) if n is not None]


def whole(value: float) -> int:
    if value != int(value):
        raise ValueError(f"{value} is not a count")
    return int(value)


# Places -------------------------------------------------------------------------------------------------------------

# Spellings the files use for a corregimiento that the archive's area codes spell another way, by district.
CORREGIMIENTO_ALIASES = {
    ("baru", "pto. armuelles (cab)"): "pto armuelles (cab)",
    ("baru", "rodolfo aguilar delgado"): "rodolfo aguilar",
    ("besiko", "soloy"): "soloy (cab)",
    ("bocas del toro", "bocas del t. (cab)"): "bocas del toro (cab)",
    ("chagres", "nvo. chagres (cab)"): "nuevo chagres (cab)",
    ("chepo", "sta. cruz de chinina"): "sta cruz de chinina",
    ("chiriqui grande", "chiriqui g. (cab)"): "chiriqui grande (cab)",
    ("cemaco", "cirilo guaynora"): "cirilo guaynora (cab)",
    ("donoso", "m. de la borda (cab)"): "miguel de la borda (cab)",
    ("jirondai", "gwaribiara"): "guariviara",
    ("jirondai", "mancreek"): "man creek",
    ("kankintu", "bisira"): "bisira (cab)",
    ("kusapin", "kusapin"): "kusapin (cab)",
    ("las minas", "qda. del rosario"): "qda del rosario",
    ("los santos", "la villa de los santos"): "la villa de los santos (cab)",
    ("mariato", "tebario"): "tebario (mariato)",
    ("mirono", "hato pilon"): "hato pilon (cab)",
    ("muna", "chichica"): "chichica (cab)",
    ("nata", "villarreal"): "villareal",
    ("nole duima", "cerro iglesias"): "cerro iglesias (cab)",
    ("pese", "sabana grande"): "sabanagrande",
    ("pinogana", "el real s.m. (cab)"): "el real de sta. maria (cab)",
    ("sambu", "rio sabalo"): "rio sabalo (cab)",
    ("san francisco", "san fco. (cab)"): "san francisco (cab)",
    ("san felix", "las lajas (cab)"): "san felix (cab)",
    ("santa fe@darien", "rio iglesias"): "rio iglesia",
    ("santiago", "la raya de sta. maria"): "la raya de sta maria",
    ("sona", "qda. de oro"): "quebrada de oro",
    ("tole", "qda. de piedra"): "qda de piedra",
    ("tonosi", "isla de canas"): "isla canas",
    ("nurun", "el piro n 2"): "el piro no. 2",
}
# Districts of the comarcas that print no district: the comarca's corregimiento is its own district.
SOLE = {
    "kuna de madungandi": "comarca kuna de madungandi",
    "kuna de wargandi": "comarca kuna de wargandi",
}
COLLIDING = {"santa fe"}


def district_code(province: str, district: str) -> str:
    code = te.place_key(district)
    return f"{code}@{te.place_key(province)}" if code in COLLIDING else code


def corregimiento_code(province: str, district: str, corregimiento: str) -> tuple[str, str]:
    """The district and corregimiento codes of a row of the 2018 tables."""
    place = te.place_key(corregimiento)
    if not district.strip():
        place = SOLE.get(place, place)
        return place, f"{place}:{place}"
    dist = district_code(province, district)
    return dist, f"{dist}:{CORREGIMIENTO_ALIASES.get((dist, place), place)}"


def circuit_code(label: object) -> str:
    return str(label).strip().replace("-", ".")


def slug(*parts: str) -> str:
    return changeset.slug(" ".join(parts))


# Checks and tables of 2023 ------------------------------------------------------------------------------------------

CIRCUIT_LABEL = re.compile(r"circuito\s+(\d+\.\d+)", re.IGNORECASE)


@dataclass
class Counts:
    reg: int
    cast: int
    valid: int
    blank: int
    null: int


@dataclass
class RM2023:
    total: Counts
    circuits: dict[str, Counts]
    candidates: list[tuple[int, str]]  # ballot position, name
    votes: dict[str, list[int]]  # per circuit, one count per candidate
    total_votes: list[int]
    circuit_valid: dict[str, int]  # the valid votes Cuadro 2 prints for each circuit


def rm_tables() -> RM2023:
    rows = grid("rm-1")
    circuits: dict[str, Counts] = {}
    total: Counts | None = None
    for cells in rows:
        label = cells[0] if isinstance(cells[0], str) else ""
        nums = numbers(cells[1:])
        if len(nums) < 15:
            continue
        counts = Counts(*(whole(nums[i]) for i in (4, 5, 9, 11, 13)))
        if label.strip().lower() == "total":
            total = counts
            continue
        found = CIRCUIT_LABEL.fullmatch(label.strip())
        if found:
            circuits[found[1]] = counts
    assert total is not None
    second = grid("rm-2")
    header = next(r for r in second if isinstance(r[2], str) and re.search(r"\(\d+\)$", r[2].strip()))
    names = [words(c) for c in header[2:] if c is not None]
    candidates = []
    for name in names:
        found = re.fullmatch(r"(.*?)\s*\((\d+)\)", name)
        assert found
        candidates.append((int(found[2]), found[1]))
    votes: dict[str, list[int]] = {}
    valid: dict[str, int] = {}
    total_votes: list[int] = []
    for cells in second:
        label = cells[0] if isinstance(cells[0], str) else ""
        nums = [whole(n) for n in numbers(cells[1:])] if len(numbers(cells[1:])) == 1 + len(candidates) else []
        if not nums:
            continue
        if label.strip().lower() == "total":
            assert nums[0] == total.valid
            total_votes = nums[1:]
            continue
        found = CIRCUIT_LABEL.fullmatch(label.strip())
        if found:
            valid[found[1]] = nums[0]
            votes[found[1]] = nums[1:]
    return RM2023(total, circuits, candidates, votes, total_votes, valid)


def cd_tables() -> tuple[pl.DataFrame, dict[str, int]]:
    """The Cambio Democrático sheet's rows by polling table, and the total row it ends with."""
    f = frame("cd-mesa")
    tables = f.filter(pl.col("MESA").is_not_null())
    total = f.filter(pl.col("MESA").is_null() & pl.col("PROVINCIA").is_null())
    if total.height != 1:
        raise ValueError(f"2018 CD: {total.height} total rows")
    return tables, {n: int(total[n][0]) for n in f.columns[7:]}


def compare() -> list[str]:
    """Check each table against the totals it prints. Raise on any difference; return what was checked."""
    said: list[str] = []
    t = rm_tables()
    if set(t.circuits) != set(t.votes):
        raise ValueError(f"the two 2023 tables name different circuits: {set(t.circuits) ^ set(t.votes)}")
    for field in ("reg", "cast", "valid", "blank", "null"):
        got = sum(getattr(c, field) for c in t.circuits.values())
        if got != getattr(t.total, field):
            raise ValueError(f"2023 {field}: circuits add up to {got}, the Total row prints {getattr(t.total, field)}")
    said.append(
        f"2023: {len(t.circuits)} circuits add up to Cuadro 1's Total row on registered, cast, valid, blank, null"
    )
    for code, c in t.circuits.items():
        if c.valid + c.blank + c.null != c.cast:
            raise ValueError(f"2023 circuit {code}: valid, blank, and null do not add up to ballots cast")
        if t.circuit_valid[code] != c.valid or sum(t.votes[code]) != c.valid:
            raise ValueError(f"2023 circuit {code}: Cuadro 2 does not add up to Cuadro 1's valid votes")
    said.append("2023: every circuit's candidate votes add up to the valid votes in Cuadro 1 and in Cuadro 2")
    sums = [sum(v[i] for v in t.votes.values()) for i in range(len(t.candidates))]
    if sums != t.total_votes or sum(sums) != t.total.valid:
        raise ValueError(f"2023 candidates add up to {sums}, the Total row prints {t.total_votes}")
    said.append(f"2023: candidate votes add up to Cuadro 2's Total row ({t.total.valid:,} valid votes)")
    if t.total.valid + t.total.blank + t.total.null != t.total.cast:
        raise ValueError("2023 total: valid, blank, and null do not add up to ballots cast")
    said.append("2023: valid, blank, and null votes add up to the ballots cast in the Total row")
    mesas, printed = cd_tables()
    names = mesas.columns[7:]
    for n in names:
        if int(mesas[n].sum()) != printed[n]:
            raise ValueError(
                f"2018 CD {n}: tables add up to {int(mesas[n].sum())}, the sheet's total row prints {printed[n]}"
            )
    if mesas["MESA"].n_unique() != mesas.height:
        raise ValueError("2018 CD: a table number repeats")
    if (mesas.select(pl.sum_horizontal(names)).to_series() > mesas["VOTANTES"]).any():
        raise ValueError("2018 CD: a table has more candidate votes than voters")
    said.append(
        f"2018 CD: {mesas.height} tables add up to the sheet's total row for every candidate ("
        + ", ".join(f"{n} {printed[n]:,}" for n in names)
        + f"); {int(mesas['VOTANTES'].sum()):,} voters, none of the tables with fewer voters than votes"
    )
    return said


# Packages -----------------------------------------------------------------------------------------------------------


def packages() -> list[str]:
    return [GEO, REGISTRY, *(f"{SOURCE}/{e.day}" for e in EVENTS)]


def geo() -> Package:
    p = Package(
        GEO,
        SOURCE,
        "The polling tables of the Tribunal Electoral's 2018 Cambio Democrático primary, and the national scheme the "
        "primaries' national counts report under",
    )
    p.external |= {PANAMA, COUNTRY}
    p.add("scheme", code=NATION, polity_id=PANAMA, kind="administrative", rank=0, noun="nation", code_system=None)
    p.add("scheme", code=MESA, polity_id=PANAMA, kind="precinct", rank=4, noun="polling table", code_system=None)
    return p


def registry() -> Package:
    p = Package(REGISTRY, SOURCE, "The electoral system that decides who a Panamanian party primary nominates")
    p.external |= {PANAMA}
    p.add("electoral_system", id=SYSTEM, polity_id=PANAMA, valid_from=None, valid_to=None, rules={"formula": "fptp"},
          rules_version=1)  # fmt: skip
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md="Precandidates are ranked by the votes they receive at the party's primary. The party's rules, not "
        "the electoral law of the general election, decide how many of them are nominated.",
        lang="en",
    )
    return p


METHOD = (
    "A nominating contest: each precandidate of the party stood on the party's primary ballot, and the Tribunal "
    "Electoral organized the vote and posted the votes of each precandidate in a spreadsheet. {extra}The files name "
    "no winners, so no outcome is stored."
)
EXTRA = {
    "unlabeled": "The spreadsheet does not say whether the count is final, so the set is stored as preliminary. ",
    "extraoficial": "The Tribunal labels the spreadsheet an unofficial count (`extraoficial`), so the set is stored as "
    "preliminary. ",
    "cd": "The spreadsheet is the count by polling table, with the voters of each table and the time its result was "
    "transmitted, and ends with a row that adds up each candidate's votes. It does not say whether the count is "
    "final, so the set is stored as preliminary. The voters of a table are stored as the ballots cast there. ",
    "rm": "The Tribunal's Cuadro 1 counts the party roll, the ballots cast, and the valid, blank, and null votes by "
    "electoral circuit, and its Cuadro 2 the votes of each precandidate, from the minutes of the national "
    "scrutiny board and the circuit boards. ",
}


@dataclass
class Row:
    name: str
    votes: int


class Builder:
    def __init__(self, event: Event) -> None:
        self.event = event
        day = event.day
        self.p = Package(
            f"{SOURCE}/{day}",
            SOURCE,
            f"The {day:%-d %B %Y} Panamanian party primaries from the Tribunal Electoral"
            if len(event.parties) > 1 or day.year != 2018
            else f"The {day:%-d %B %Y} Panamanian party primary from the Tribunal Electoral",
            depends=[GEO, REGISTRY],
        )
        p = self.p
        p.external |= {PANAMA, COUNTRY, CIRCUIT, DISTRICT, CORREGIMIENTO, SYSTEM}
        p.external |= {te.PRESIDENCY, te.MAYORALTY, te.REPRESENTATIVE, te.BODY_ASSEMBLY, te.BODY_COUNCIL}
        self.source = key("source", day)
        p.add(
            "source",
            id=self.source,
            kind="official_body",
            publisher="Tribunal Electoral de Panamá",
            title=f"Resultados de las elecciones primarias, {day.year}",
            url=PAGE,
            license="Publicly posted official results; no license stated",
            license_url=SITE,
            attribution="Tribunal Electoral de Panamá",
        )
        for name in event.files:
            p.artifact(raw.cached(SOURCE, F[name]), self.source)
        self.election = key("election", day)
        p.add("election", id=self.election, polity_id=PANAMA, kind="primary", date_from=day, date_to=day, status="held")
        p.name(self.election, event.english, "en", "official")
        p.name(self.election, event.spanish, "es", "native", False)
        self.base = f"pa/{day}-primarias"
        p.add("slug", path=self.base, entity_id=self.election, is_current=True)
        for party in event.parties:
            p.external.add(te.party_id(party.code))
        self.units: dict[str, str] = {}
        self.slugs: set[str] = set()

    # Units
    def unit(self, scheme: str, code: str, area: str | None, relation: str = "equals", label: str | None = None) -> str:
        found = f"{scheme}:{code}"
        if found not in self.units:
            uid = self.units[found] = key("unit", self.event.day, scheme, code)
            if area:
                self.p.external.add(area)
            self.p.add("reporting_unit", id=uid, polity_id=PANAMA, scheme_code=scheme, code=f"{self.event.day}:{code}",
                       area_relation=relation, area_id=area, kind="standard")  # fmt: skip
            if label:
                self.p.name(uid, label, "es", "official")
        return self.units[found]

    # Contests
    def contest(
        self, party: Party, office: str, tag: str, area: str, en: str, es: str, slug_tail: str, note: str, stage: str
    ) -> tuple[str, str]:
        p = self.p
        cid = key("contest", self.event.day, party.code, office, tag)
        fields: dict[str, object]
        if office in ("president", "mayor", "rep"):
            fields = {"kind": "office", "magnitude": 1, "office_id": {"president": te.PRESIDENCY, "mayor": te.MAYORALTY,
                      "rep": te.REPRESENTATIVE}[office]}  # fmt: skip
        else:
            fields = {"kind": "body_seats", "body_id": te.BODY_ASSEMBLY if office == "deputy" else te.BODY_COUNCIL}
        p.add("contest", id=cid, election_id=self.election, district_area_id=area, round_number=1,
              electoral_system_id=SYSTEM, default_result_kind="counted", **fields)  # fmt: skip
        p.name(cid, f"{party.code} primary: {en}", "en", "official")
        p.name(cid, f"Primarias de {party.name}: {es}", "es", "native", False)
        path = f"{self.base}/{party.slug}/{slug_tail}"
        if path in self.slugs:
            raise ValueError(f"two contests take the slug {path}")
        self.slugs.add(path)
        p.add("slug", path=path, entity_id=cid, is_current=True)
        rid = key("result_set", self.event.day, party.code, office, tag)
        p.add("result_set", id=rid, election_id=self.election, contest_id=cid, stage=stage, kind="counted",
              source_id=self.source, status="active", is_default=True, completeness=1.0)  # fmt: skip
        p.add("note", id=key("note", rid), entity_id=rid, kind="methodology", lang="en",
              body_md=METHOD.format(extra=note))  # fmt: skip
        return cid, rid

    def options(self, party: Party, cid: str, names: Sequence[tuple[int | None, str]]) -> dict[str, str]:
        """One option and candidacy for each candidate, keyed by name."""
        p = self.p
        out: dict[str, str] = {}
        for index, (position, name) in enumerate(names, 1):
            if name in out:
                raise ValueError(f"{cid}: {name} is on the ballot twice")
            oid = out[name] = key("option", cid, index, name)
            person = key("person", cid, index, name)
            p.add("contest_option", id=oid, contest_id=cid, kind="candidate", party_id=te.party_id(party.code),
                  ballot_position=position, ballot_label=name)  # fmt: skip
            p.add("person", id=person)
            p.name(person, name, "es", "official")
            p.add("candidacy", id=key("candidacy", cid, index, name), person_id=person, option_id=oid, role="head",
                  ballot_name=name, member_of_party_id=te.party_id(party.code), is_independent=False)  # fmt: skip
        return out

    def votes(self, rid: str, uid: str, oid: str, value: int) -> None:
        self.p.add("result_value", result_set_id=rid, unit_id=uid, option_id=oid, channel=0, measure=VOTES,
                   count_round=0, flags=0, value=value)  # fmt: skip

    def total(self, rid: str, measure: int, value: int, oid: str | None = None) -> None:
        self.p.add("declared", result_set_id=rid, option_id=oid, measure=measure, value=value)

    # A national count of a presidential primary
    def national(self, party: Party, file: str, note: str, stage: str) -> None:
        names, counts = self.read_national(file)
        cid, rid = self.contest(party, "president", "", NATIONAL_AREA, "President", "Presidente", "presidente", note,
                                stage)  # fmt: skip
        options = self.options(party, cid, [(None, n) for n in names])
        uid = self.unit(NATION, "PA", NATIONAL_AREA, label="Panamá")
        for name, value in zip(names, counts, strict=True):
            self.votes(rid, uid, options[name], value)

    @staticmethod
    def read_national(file: str) -> tuple[list[str], list[int]]:
        f = frame(file)
        name_col = "alias" if "alias" in f.columns else "NOMBRES"
        votes_col = "votos" if "votos" in f.columns else "TOTALES"
        return [person_name(n) for n in f[name_col]], [int(v) for v in f[votes_col]]

    # Contests by place: a file with a place, a name, and votes on each row.
    def by_place(
        self,
        party: Party,
        office: str,
        file: str,
        note: str,
        stage: str,
        place_of: Callable[[dict[str, object]], Place],
    ) -> None:
        f = frame(file)
        name_col = next(c for c in f.columns if c in ("CANDIDATO", "alias"))
        votes_col = next(c for c in f.columns if c in ("VOTOS", "votos"))
        groups: dict[str, list[Row]] = {}
        places: dict[str, Place] = {}
        for r in f.iter_rows(named=True):
            if r[name_col] is None:
                continue
            place = place_of(r)
            places[place.code] = place
            groups.setdefault(place.code, []).append(Row(person_name(r[name_col]), int(r[votes_col])))
        label_en, label_es, tail = OFFICES[office]
        for code, rows in sorted(groups.items()):
            place = places[code]
            cid, rid = self.contest(party, office, code, place.area, label_en + place.label, label_es + place.label,
                                    f"{tail}/{slug(code)}", note, stage)  # fmt: skip
            options = self.options(party, cid, [(None, row.name) for row in rows])
            uid = self.unit(place.scheme, code, place.area)
            for row in rows:
                self.votes(rid, uid, options[row.name], row.votes)


OFFICES = {
    "deputy": ("Deputies, circuit ", "Diputados, circuito ", "diputados"),
    "mayor": ("Mayor of ", "Alcalde de ", "alcaldes"),
    "rep": ("Representative of ", "Representante de ", "representantes"),
    "council": ("Councillors of ", "Concejales de ", "concejales"),
}


@dataclass(frozen=True)
class Place:
    code: str
    area: str
    scheme: str
    label: str


def circuit_place(r: dict[str, object]) -> Place:
    code = circuit_code(r["CIRCUITO"])
    return Place(code, te.area(CIRCUIT, code), CIRCUIT, code)


def district_place(r: dict[str, object]) -> Place:
    code = district_code(str(r["PROVINCIA"]), str(r["DISTRITO"]))
    label = te.pretty(words(r["DISTRITO"]))
    if code in {f"{c}@{p}" for c in COLLIDING for p in PROVINCES}:
        label += f" ({te.pretty(words(r['PROVINCIA']))})"
    return Place(code, te.area(DISTRICT, code), DISTRICT, label)


def corregimiento_place(r: dict[str, object]) -> Place:
    _, code = corregimiento_code(str(r["PROVINCIA"]), str(r["DISTRITO"]), str(r["CORREGIMIENTO"]))
    label = te.pretty(words(r["CORREGIMIENTO"]))
    if str(r["DISTRITO"]).strip():
        label += f", {te.pretty(words(r['DISTRITO']))}"
    return Place(code, te.area(CORREGIMIENTO, code), CORREGIMIENTO, label)


PROVINCES = {"bocas del toro", "cocle", "colon", "chiriqui", "darien", "herrera", "los santos", "panama", "veraguas",
             "panama oeste", "embera", "ngabe bugle"}  # fmt: skip


def stage_note(file: str) -> str:
    if file.startswith("prd-"):
        return EXTRA["extraoficial"]
    return EXTRA["unlabeled"]


def cd(b: Builder) -> None:
    """The Cambio Democrático presidential primary, by polling table."""
    f, printed = cd_tables()
    names = f.columns[7:]
    labels = {n: person_name(n) for n in names}
    cid, rid = b.contest(CD, "president", "", NATIONAL_AREA, "President", "Presidente", "presidente", EXTRA["cd"],
                         b.event.stage)  # fmt: skip
    options = b.options(CD, cid, [(None, labels[n]) for n in names])
    for r in f.iter_rows(named=True):
        _, code = corregimiento_code(str(r["PROVINCIA"]), str(r["DISTRITO"]), str(r["CORREGIMIENTO"]))
        label = f"Mesa {r['MESA']}, {te.pretty(words(r['CENTRO']))}, {te.pretty(words(r['CORREGIMIENTO']))}"
        uid = b.unit(MESA, str(r["MESA"]), te.area(CORREGIMIENTO, code), "within", label)
        b.p.add("result_value", result_set_id=rid, unit_id=uid, option_id=None, channel=0, measure=CAST, count_round=0,
                flags=0, value=int(r["VOTANTES"]))  # fmt: skip
        for n in names:
            b.votes(rid, uid, options[labels[n]], int(r[n]))
    for n in names:
        b.total(rid, VOTES, printed[n], options[labels[n]])


def party_files(b: Builder, party: Party, prefix: str) -> None:
    stage = b.event.stage
    b.national(party, f"{prefix}-president", stage_note(f"{prefix}-"), stage)
    note = stage_note(f"{prefix}-")
    if f"{prefix}-deputy" in F:
        b.by_place(party, "deputy", f"{prefix}-deputy", note, stage, circuit_place)
    b.by_place(party, "mayor", f"{prefix}-mayor", note, stage, district_place)
    b.by_place(party, "rep", f"{prefix}-rep", note, stage, corregimiento_place)
    if f"{prefix}-council" in F:
        b.by_place(party, "council", f"{prefix}-council", note, stage, district_place)


def rm(b: Builder) -> None:
    t = rm_tables()
    cid, rid = b.contest(RM, "president", "", NATIONAL_AREA, "President", "Presidente", "presidente", EXTRA["rm"],
                         b.event.stage)  # fmt: skip
    options = b.options(RM, cid, [(position, person_name(name)) for position, name in t.candidates])
    ids = [options[person_name(name)] for _, name in t.candidates]
    for code, c in sorted(t.circuits.items(), key=lambda item: [int(x) for x in item[0].split(".")]):
        uid = b.unit(CIRCUIT, code, te.area(CIRCUIT, code))
        for measure, value in (
            (REGISTERED, c.reg),
            (CAST, c.cast),
            (VALID, c.valid),
            (INVALID, c.null),
            (BLANK, c.blank),
        ):
            b.p.add("result_value", result_set_id=rid, unit_id=uid, option_id=None, channel=0, measure=measure,
                    count_round=0, flags=0, value=value)  # fmt: skip
        for oid, value in zip(ids, t.votes[code], strict=True):
            b.votes(rid, uid, oid, value)
    c = t.total
    for measure, value in ((REGISTERED, c.reg), (CAST, c.cast), (VALID, c.valid), (INVALID, c.null), (BLANK, c.blank)):
        b.total(rid, measure, value)
    for oid, value in zip(ids, t.total_votes, strict=True):
        b.total(rid, VOTES, value, oid)


def election(event: Event) -> Package:
    b = Builder(event)
    day = event.day
    if day == date(2018, 8, 12):
        cd(b)
    elif day == date(2018, 9, 16):
        party_files(b, PRD, "prd")
    elif day == date(2018, 10, 7):
        b.national(ALIANZA, "alianza-president", EXTRA["unlabeled"], event.stage)
    elif day == date(2018, 10, 28):
        party_files(b, PAN, "pan")
        b.national(FAD, "fad-president", EXTRA["unlabeled"], event.stage)
    else:
        rm(b)
    return b.p


def parse() -> None:
    for line in compare():
        print(line, flush=True)
    geo().write()
    registry().write()
    for event in EVENTS:
        election(event).write()


def emit() -> None:
    package.run("load", packages())
