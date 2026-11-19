"""Romanian elections of 2024 and 2025 by polling station, from the Permanent Electoral Authority's results portal.

The portal at prezenta.roaep.ro publishes, for each election, one JSON file per county with the figures of every
polling station's signed record (procès-verbal), and, for local elections, the records of each electoral
constituency and county bureau, including the seats they allocate. The site refuses scripted requests, so each
election's files were saved from a browser into one JSON object keyed by the path of each file on the site."""

import json
import re
from dataclasses import dataclass, field
from datetime import date
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.sources.ro import prezenta_seats

SOURCE = "ro.prezenta"
ROMANIA = str(uuid5(package.NAMESPACE, "polity\x1fRO"))
SITE = "https://prezenta.roaep.ro"
VOTES, REGISTERED, CAST, VALID, INVALID, BLANK = 1, 2, 3, 4, 5, 6
POSTAL = 3
ABROAD = "43"
INDEPENDENT = "CANDIDAT INDEPENDENT"
BUCHAREST = "42"

URLS = {
    "europarlamentare09062024": f"{SITE}/europarlamentare09062024/data/json/sicpv/",
    "locale09062024v2": f"{SITE}/locale09062024v2/data/json/sicpv/",
    "locale23062024": f"{SITE}/locale23062024/data/json/sicpv/",
    "locale07072024v2": f"{SITE}/locale07072024v2/data/json/sicpv/",
    "prezidentiale24112024": f"{SITE}/prezidentiale24112024/data/json/sicpv/",
    "parlamentare01122024": f"{SITE}/parlamentare01122024/data/json/sicpv/",
    "prezidentiale04052025": f"{SITE}/prezidentiale04052025/data/json/sicpv/",
    "locale04052025": f"{SITE}/locale04052025/data/json/sicpv/",
    "prezidentiale18052025": f"{SITE}/prezidentiale18052025/data/json/sicpv/",
    "locale07122025": f"{SITE}/locale07122025/data/json/sicpv/",
}
BEC_RUNOFF = "https://prezidentiale2025.bec.ro/wp-content/uploads/2025/05/RFT1_2.pdf"
BEC_LOCAL = tuple(f"https://locale2024.bec.ro/wp-content/uploads/2024/07/{name}.pdf" for name in ("PV_P", "PV_PCJ"))
RUNOFF_WINNER = "NICUȘOR-DANIEL DAN"
# the Central Electoral Bureau's record of 2024 for the county council presidents (point i): the mandates won by each
# party or alliance, which the county presidents declared elected on 9 June 2024 must add up to
PRESIDENT_MANDATES = {
    "PARTIDUL SOCIAL DEMOCRAT": 24,
    "PARTIDUL NAȚIONAL LIBERAL": 11,
    "UNIUNEA DEMOCRATĂ MAGHIARĂ DIN ROMÂNIA": 4,
    "ALIANȚA ELECTORALĂ PSD PNL": 2,
}
# its record for the mayors (point g), which covers the rounds of 9 June, 23 June, and 7 July 2024
MAYOR_MANDATES = 3186
FOLD = str.maketrans({"Ş": "Ș", "ş": "ș", "Ţ": "Ț", "ţ": "ț", "“": "", "”": "", '"': "", "–": "-", "„": ""})
DISPLAY = str.maketrans({"Ş": "Ș", "ş": "ș", "Ţ": "Ț", "ţ": "ț"})
ORGANIZATION = re.compile(
    r"PARTID|ALIAN|UNIUNEA|ASOCIA|FORUM|COMUNIT|FEDERA|LIGA|ORGANIZA|MIȘCAREA|ROMÂNI|BLOC|FRONT|NAȚION|DEMOCRA|"
    r"CONSERV|ECOLOG|INDEPENDENT|\bUSR\b|\bPSD\b|\bPNL\b|\bAUR\b|\bPMP\b|S\.O\.S|REPER|DREAPT|LISTA|CETĂȚEN|"
    r"GRUP|UNIT|SOCIAL|LIBERAL"
)
PERSON = re.compile(r"[A-ZĂÂÎȘȚÁÉÍÓÖŐÚÜŰČĆŠŽ' .-]+")
METHOD = (
    "Counted per polling station and published by the Permanent Electoral Authority on its results portal, from "
    "each station's signed record. Registered voters are the voters on the permanent, supplementary, and special "
    "lists, ballots cast are the voters who took part, valid votes are the votes for lists and candidates, invalid "
    "ballots are the null votes, and, where the record prints them, blank ballots are the votes cast blank. Stations "
    "abroad print no permanent list, so their registered voters are left empty where the record prints zero."
)
POST_METHOD = (
    "Votes cast by post from abroad are counted by the Authority in units of their own, apart from the polling "
    "stations. For them the ballots cast are the ballots counted, the valid votes are the valid ballots among them, "
    "and the invalid ballots are the null ones."
)
LOCAL_METHOD = (
    "Counted per polling station and published by the Permanent Electoral Authority on its results portal, from "
    "each station's signed record. Registered voters are the voters on the lists, ballots cast are the voters who "
    "took part, valid votes are the votes for lists and candidates, and invalid ballots are the null votes. The "
    "declared totals and the seats are those of the constituency's or county's electoral bureau, as the portal "
    "prints them; a council's seats add the seats of both rounds of the allocation and any seat given to a national "
    "minority organization."
)
ANNULLED = (
    "The Constitutional Court annulled the whole presidential election on 6 December 2024 (Decision 32/2024), two "
    "days before the second round was due, and the election was held again from 4 May 2025. The figures are those "
    "the Authority counted for the first round of 24 November 2024."
)


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


AEP_SOURCE = key("source", "aep")
BEC_SOURCE = key("source", "bec")
PRESIDENT = key("office", "president")
MAYOR = key("office", "mayor")
GENERAL_MAYOR = key("office", "general-mayor")
COUNTY_PRESIDENT = key("office", "county-council-president")
CHAMBER = key("body", "chamber")
SENATE = key("body", "senate")
EUROPEAN_PARLIAMENT = key("body", "european-parliament")
LOCAL_COUNCIL = key("body", "local-council")
COUNTY_COUNCIL = key("body", "county-council")
BUCHAREST_COUNCIL = key("body", "bucharest-council")
PLURALITY = key("electoral_system", "plurality")
TWO_ROUND = key("electoral_system", "two-round")


def canon(text: object) -> str:
    value = " ".join(str(text or "").translate(FOLD).split()).upper()
    return re.sub(r"\s*-\s*", "-", value)


def tidy(text: object) -> str:
    return " ".join(str(text or "").translate(DISPLAY).split())


def title(text: object) -> str:
    return tidy(text).title()


def ident(label: str, party: str | None) -> str:
    """A ballot line's key: its label, and its nominating party, since namesakes stand for one mayoralty."""
    return canon(label) + (f"|{canon(party)}" if party else "")


def party_id(label: str) -> str:
    return key("party", canon(label))


def is_person(label: str, parties: set[str]) -> bool:
    """Whether a ballot line on a list ballot is an independent candidate rather than a party or alliance."""
    text = canon(label)
    return text not in parties and not ORGANIZATION.search(text) and bool(PERSON.fullmatch(text)) and " " in text


def nominator(line: dict[str, object]) -> str | None:
    """The party that nominated a candidate, or None for an independent, whom the portal marks as such."""
    party = line.get("party")
    if not party or canon(party) == INDEPENDENT:
        return None
    return str(party)


def whole(value: object) -> int | None:
    text = str(value if value is not None else "").strip()
    if text in ("", "None", "-"):
        return None
    return int(float(text))


def position(line: dict[str, object]) -> int:
    """The ballot position the portal codes as `P<n>-P`, or 0."""
    found = re.fullmatch(r"P(\d+)-P", str(line.get("id") or ""))
    return int(found.group(1)) if found else 0


def fetch() -> None:
    """The portal refuses scripted requests; the files were saved from a browser and stored with `raw.store`."""
    for url in URLS.values():
        raw.fetch(SOURCE, url, suffix=".json")
    for url in (*prezenta_seats.URLS, BEC_RUNOFF, *BEC_LOCAL):
        raw.fetch(SOURCE, url, suffix=".pdf")


@dataclass
class Row:
    scope: str
    code: str
    category: str
    stage: str
    created: str
    county: str | None
    county_name: str | None
    station_county: str | None
    uat: str | None
    uat_name: str | None
    nr: str | None
    station: str | None
    station_id: str | None
    fields: dict[str, int | None]
    lines: list[dict[str, object]] = field(default_factory=list)


def records(name: str) -> list[Row]:
    """Every record in the saved files of one election, once each, keeping the latest version of a record."""
    data = json.loads(raw.cached(SOURCE, URLS[name]).path.read_bytes())
    found: dict[tuple[object, ...], Row] = {}
    versions: dict[tuple[object, ...], tuple[str, int]] = {}
    for path, body in data.items():
        if "/pv/pv_" not in path or "aggregated" in path or not isinstance(body, dict):
            continue
        blocks = [body["scopes"]] if "scopes" in body else []
        blocks += [stage["scopes"] for stage in (body.get("stages") or {}).values() if stage.get("scopes")]
        for scopes in blocks:
            for scope, block in scopes.items():
                for category, part in block.get("categories", {}).items():
                    for item in (part.get("table") or {}).values():
                        fields: dict[str, int | None] = {}
                        for cell in item["fields"]:
                            fields.setdefault(cell["name"], whole(cell["value"]))
                        row = Row(
                            scope=scope,
                            code=item["report_type_code"],
                            category=category,
                            stage=item.get("report_stage_code") or "",
                            created=item.get("report_created_at") or "",
                            county=item.get("county_nce"),
                            county_name=item.get("county_name"),
                            station_county=item.get("precinct_county_nce"),
                            uat=item.get("uat_siruta"),
                            uat_name=item.get("uat_name"),
                            nr=(item.get("precinct_nr") or "").strip() or None,
                            station=item.get("precinct_name"),
                            station_id=item.get("precinct_id"),
                            fields=fields,
                            lines=list(item.get("candidates") or item.get("votes") or []),
                        )
                        identity = (row.code, row.county, row.uat, item.get("precinct_id"))
                        version = (row.created, int(item.get("report_version") or 0))
                        if identity not in versions or version > versions[identity]:
                            found[identity] = row
                            versions[identity] = version
    return list(found.values())


@dataclass
class Contest:
    key: str
    election: str
    english: str
    romanian: str
    slug: str
    kind: str
    body: str | None = None
    office: str | None = None
    magnitude: int | None = None
    system: str | None = None
    round_number: int = 1
    previous: str | None = None
    method: str = METHOD
    # the contest of the EU-wide record this one supersedes: (election id, contest id, result set id, source id)
    linked: tuple[str, str, str, str] | None = None


@dataclass
class Build:
    """What one election package stages: its units, contests, ballot lines, and values, before they become frames."""

    p: Package
    day: str
    parties: set[str]
    units: dict[str, tuple[str, str, str]] = field(default_factory=dict)
    contests: dict[str, Contest] = field(default_factory=dict)
    options: dict[tuple[str, str], tuple[str, str, str | None, int]] = field(default_factory=dict)
    independent: dict[str, bool | None] = field(default_factory=dict)
    values: list[tuple[str, str, str | None, int, int, int]] = field(default_factory=list)
    declared: list[tuple[str, str | None, int, int]] = field(default_factory=list)
    elected: list[tuple[str, str]] = field(default_factory=list)
    seats: dict[tuple[str, str], int] = field(default_factory=dict)
    people: dict[str, tuple[str, str]] = field(default_factory=dict)
    labels: set[str] = field(default_factory=set)
    shared: dict[str, str] = field(default_factory=dict)

    def unit(self, row: Row, postal: bool = False) -> str:
        county = row.station_county or row.county or ""
        # postal bureaus abroad repeat their numbers from one country to the next, so they go by the portal's id
        code = f"{self.day}:{county}/c{row.station_id}" if postal else f"{self.day}:{county}/{row.nr}"
        if code not in self.units:
            kind = "postal_district" if postal else "abroad_mission" if county == ABROAD else "standard"
            where = title(row.uat_name) if row.uat_name else title(row.county_name)
            what = "postal votes" if postal else "station"
            text = f"{tidy(row.station)} ({where}, {what} {row.nr})" if row.station else f"{where}, {what} {row.nr}"
            self.units[code] = (key("unit", code), kind, text)
        return self.units[code][0]

    def contest(self, contest: Contest) -> None:
        self.contests.setdefault(contest.key, contest)

    def option(
        self,
        contest: str,
        label: str,
        party: str | None,
        person: bool,
        position: int = 0,
        independent: bool | None = None,
    ) -> str:
        """The ballot line `label` of `contest`; `party` is the nominating party of a candidate, if any."""
        text = ident(label, party)
        found = self.options.get((contest, text))
        if found:
            return found[0]
        option = key("option", contest, text)
        if not person:
            self.labels.add(tidy(label))
        if party:
            self.labels.add(tidy(party))
        self.options[(contest, text)] = (option, tidy(label), party if person else label, position)
        if person:
            self.people[option] = (self.person(contest, text), title(label))
            if self.contests[contest].office == PRESIDENT:
                self.shared[self.people[option][0]] = title(label)
            self.independent[option] = independent
        return option

    def person(self, contest: str, text: str) -> str:
        office = self.contests[contest].office
        if office == PRESIDENT:
            return key("person", "president", text)
        return key("person", contest, text)

    def record(self, contest: str, unit: str, accounting: dict[str, int | None], postal: bool) -> None:
        channel = POSTAL if postal else 0
        for measure, value in (
            (REGISTERED, accounting.get("registered")),
            (CAST, accounting.get("cast")),
            (VALID, accounting.get("valid")),
            (INVALID, accounting.get("invalid")),
            (BLANK, accounting.get("blank")),
        ):
            if value is not None:
                self.values.append((contest, unit, None, channel, measure, value))

    def stage(self) -> None:
        p = self.p
        p.frame(
            "reporting_unit",
            pl.DataFrame(
                {
                    "id": [u[0] for u in self.units.values()],
                    "polity_id": ROMANIA,
                    "scheme_code": "ro.sv",
                    "code": list(self.units),
                    "area_relation": "unlocated",
                    "kind": [u[1] for u in self.units.values()],
                }
            ),
        )
        p.frame(
            "name",
            pl.DataFrame(
                {
                    "id": [p.id("name", u[0], "ro", "official", u[2]) for u in self.units.values()],
                    "entity_id": [u[0] for u in self.units.values()],
                    "text": [u[2] for u in self.units.values()],
                    "lang": "ro",
                    "kind": "official",
                    "is_preferred": True,
                }
            ),
        )
        contest_ids = {name: c.linked[1] if c.linked else key("contest", name) for name, c in self.contests.items()}
        set_ids = {name: key("result_set", name) for name in self.contests}
        contests = list(self.contests.values())
        fresh = [c for c in contests if not c.linked]
        p.frame(
            "contest",
            pl.DataFrame(
                {
                    "id": [contest_ids[c.key] for c in fresh],
                    "election_id": [c.election for c in fresh],
                    "kind": [c.kind for c in fresh],
                    "office_id": [c.office for c in fresh],
                    "body_id": [c.body for c in fresh],
                    "magnitude": [c.magnitude for c in fresh],
                    "round_number": [c.round_number for c in fresh],
                    "previous_round_id": [c.previous for c in fresh],
                    "electoral_system_id": [c.system for c in fresh],
                    "default_result_kind": "counted",
                },
                schema_overrides={"magnitude": pl.Int32, "round_number": pl.Int16},
            ),
        )
        names = [(contest_ids[c.key], c.english, "en", "official", True) for c in fresh]
        names += [(contest_ids[c.key], c.romanian, "ro", "native", False) for c in fresh]
        p.frame(
            "name",
            pl.DataFrame(
                {
                    "id": [p.id("name", n[0], n[2], n[3], n[1]) for n in names],
                    "entity_id": [n[0] for n in names],
                    "text": [n[1] for n in names],
                    "lang": [n[2] for n in names],
                    "kind": [n[3] for n in names],
                    "is_preferred": [n[4] for n in names],
                }
            ),
        )
        p.frame(
            "slug",
            pl.DataFrame(
                {
                    "path": [c.slug for c in fresh],
                    "entity_id": [contest_ids[c.key] for c in fresh],
                    "is_current": True,
                }
            ),
        )
        p.frame(
            "result_set",
            pl.DataFrame(
                {
                    "id": [set_ids[c.key] for c in contests],
                    "election_id": [c.election for c in contests],
                    "contest_id": [contest_ids[c.key] for c in contests],
                    "stage": "official",
                    "kind": "counted",
                    "source_id": AEP_SOURCE,
                    "status": "active",
                    "is_default": True,
                    "completeness": 1.0,
                    "supersedes_id": [c.linked[2] if c.linked else None for c in contests],
                },
                schema_overrides={"supersedes_id": pl.String},
            ),
        )
        for c in contests:
            if c.linked:
                superseded(p, c.linked, set_ids[c.key])
        notes = [(set_ids[c.key], c.method) for c in contests]
        posted = {contest for contest, _, _, channel, _, _ in self.values if channel == POSTAL}
        notes += [(set_ids[name], POST_METHOD) for name in self.contests if name in posted]
        p.frame(
            "note",
            pl.DataFrame(
                {
                    "id": [key("note", entity, text[:24]) for entity, text in notes],
                    "entity_id": [entity for entity, _ in notes],
                    "kind": "methodology",
                    "body_md": [text for _, text in notes],
                    "lang": "en",
                }
            ),
        )
        lines = list(self.options.items())
        p.frame(
            "contest_option",
            pl.DataFrame(
                {
                    "id": [o[0] for _, o in lines],
                    "contest_id": [contest_ids[c] for (c, _), _ in lines],
                    "kind": ["candidate" if o[0] in self.people else "list" for _, o in lines],
                    "party_id": [party_id(o[2]) if o[2] else None for _, o in lines],
                    "ballot_position": [o[3] or None for _, o in lines],
                    "ballot_label": [o[1] for _, o in lines],
                },
                schema_overrides={"ballot_position": pl.Int32},
            ),
        )
        # presidential candidates stand in more than one election, so the registry holds them
        people = {who: text for who, text in self.people.values() if who not in self.shared}
        p.frame("person", pl.DataFrame({"id": list(people)}))
        p.frame(
            "name",
            pl.DataFrame(
                {
                    "id": [p.id("name", who, "ro", "official", text) for who, text in people.items()],
                    "entity_id": list(people),
                    "text": list(people.values()),
                    "lang": "ro",
                    "kind": "official",
                    "is_preferred": True,
                }
            ),
        )
        p.frame(
            "candidacy",
            pl.DataFrame(
                {
                    "id": [key("candidacy", option) for option in self.people],
                    "person_id": [who for who, _ in self.people.values()],
                    "option_id": list(self.people),
                    "role": "head",
                    "ballot_name": [text for _, text in self.people.values()],
                    "is_independent": [self.independent[option] for option in self.people],
                }
            ),
        )
        frame = pl.DataFrame(
            self.values,
            schema={
                "contest": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        )
        p.frame(
            "result_value",
            frame.select(
                pl.col("contest").replace_strict(set_ids).alias("result_set_id"),
                "unit_id",
                "option_id",
                "channel",
                "measure",
                pl.lit(0, pl.Int16).alias("count_round"),
                pl.lit(0, pl.Int16).alias("flags"),
                "value",
            ),
        )
        if self.declared:
            p.frame(
                "declared",
                pl.DataFrame(
                    {
                        "result_set_id": [set_ids[d[0]] for d in self.declared],
                        "option_id": [d[1] for d in self.declared],
                        "measure": [d[2] for d in self.declared],
                        "value": [d[3] for d in self.declared],
                    },
                    schema_overrides={"measure": pl.Int16, "value": pl.Int64},
                ),
            )
        outcomes = [(c, o, "won", n) for (c, o), n in self.seats.items()] + [
            (c, o, "elected", 1) for c, o in self.elected
        ]
        if outcomes:
            p.frame(
                "outcome",
                pl.DataFrame(
                    {
                        "result_set_id": [set_ids[c] for c, _, _, _ in outcomes],
                        "option_id": [o for _, o, _, _ in outcomes],
                        "kind": [k for _, _, k, _ in outcomes],
                        "seats": [n for _, _, _, n in outcomes],
                    },
                    schema_overrides={"seats": pl.Int32},
                ),
            )


def election(
    p: Package,
    name: str,
    day: date,
    kind: str,
    english: str,
    romanian: str,
    path: str,
    status: str = "held",
    rerun: str | None = None,
) -> str:
    id = key("election", name)
    p.add(
        "election",
        id=id,
        polity_id=ROMANIA,
        kind=kind,
        date_from=day,
        date_to=day,
        date_precision="day",
        status=status,
        rerun_of_id=rerun,
    )
    p.add("slug", path=path, entity_id=id, is_current=True)
    p.name(id, english, "en", "official")
    p.name(id, romanian, "ro", "native", False)
    return id


def _external(scheme: str, value: str) -> str:
    return str(uuid5(package.NAMESPACE, f"external_id:{scheme}\x1f{value}"))


# The archive holds the 2024 European Parliament election in Romania as a national result set, from the pipeline that
# covers every member state. The polling-station set supersedes it and reuses its election and contest.
EU_SOURCE_PUBLISHER = "Biroul Electoral Central"
EU_SOURCE_TITLE = (
    "Proces-verbal privind centralizarea voturilor și atribuirea mandatelor la alegerile pentru membrii din România "
    "în Parlamentul European din data de 09 iunie 2024"
)
EU_RECORD = (
    _external("eu.election", "RO:2024:european-parliament"),
    _external("eu.contest", "RO:2024:eu.member_state:RO"),
    _external("eu.ep2024.result_set", "RO:2024"),
    str(uuid5(package.NAMESPACE, f"source\x1f{EU_SOURCE_PUBLISHER}\x1f{EU_SOURCE_TITLE}")),
)


def superseded(p: Package, eu: tuple[str, str, str, str], result_set: str) -> None:
    """Keep the EU-wide national totals as a superseded set, re-staged with its own election, contest, and source."""
    election, contest, old, source = eu
    p.add(
        "result_set",
        id=old,
        election_id=election,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source,
        status="superseded",
        is_default=False,
        completeness=None,
    )
    p.add(
        "note",
        id=key("note", result_set, "supersedes"),
        entity_id=result_set,
        kind="methodology",
        body_md="This set holds the same contest by polling station. It supersedes the set of national totals.",
        lang="en",
    )


def accept(p: Package) -> None:
    p.accept(
        "accounting.ballots_sum",
        "some station records print valid and null votes that do not add up to the voters who took part, because "
        "voters left without casting a ballot or the record was signed with the difference; stored as printed",
    )
    p.accept(
        "accounting.turnout",
        "some stations, mostly those voting on supplementary and special lists, print more voters than the voters "
        "on their lists; stored as printed",
    )


def package_of(key_: str, summary: str, depends: list[str] | None = None) -> Package:
    p = Package(f"{SOURCE}/{key_}", SOURCE, summary, depends=[f"{SOURCE}/registry", *(depends or [])])
    p.external.add(ROMANIA)
    p.external.add("ro.sv")
    return p


# Presidential elections


PRESIDENTIAL = {
    "2024-11-24": ("prezidentiale24112024", date(2024, 11, 24), 1),
    "2025-05-04": ("prezidentiale04052025", date(2025, 5, 4), 1),
    "2025-05-18": ("prezidentiale18052025", date(2025, 5, 18), 2),
}


def presidential(day: str, parties: set[str]) -> tuple[Package, Build]:
    name, first, round_number = PRESIDENTIAL[day]
    runoff = round_number == 2
    p = package_of(
        f"{day}-presidential",
        f"The Romanian presidential election of {first}"
        + (", second round," if runoff else "")
        + " by polling station, from the Permanent Electoral Authority's results portal",
        {
            "2025-05-18": ["ro.prezenta/2025-05-04-presidential"],
            "2025-05-04": ["ro.prezenta/2024-11-24-presidential"],
        }.get(day, []),
    )
    p.artifact(raw.cached(SOURCE, URLS[name]), AEP_SOURCE)
    annulled = day == "2024-11-24"
    eid = election(
        p,
        f"president-{day}",
        first,
        "runoff" if runoff else "general",
        f"{first.year} Romanian presidential election" + (", second round" if runoff else ""),
        f"Alegerea Președintelui României din {first.day:02d}.{first.month:02d}.{first.year}"
        + (", turul al doilea" if runoff else ""),
        f"ro/{day}-presidential" + ("-runoff" if runoff else ""),
        status="annulled" if annulled else "held",
        rerun=key("election", "president-2024-11-24") if day == "2025-05-04" else None,
    )
    if annulled:
        p.add("note", id=key("note", eid, "annulled"), entity_id=eid, kind="caveat", body_md=ANNULLED, lang="en")
    b = Build(p, day, parties)
    contest = f"president-{day}"
    b.contest(
        Contest(
            contest,
            eid,
            f"{first.year} Romanian presidential election" + (", second round" if runoff else ""),
            "Președintele României" + (", turul al doilea" if runoff else ""),
            f"ro/{day}-presidential" + ("-runoff" if runoff else "") + "/president",
            "office",
            office=PRESIDENT,
            magnitude=1,
            system=TWO_ROUND,
            round_number=round_number,
            previous=key("contest", "president-2025-05-04") if runoff else None,
        )
    )
    for row in records(name):
        if row.scope != "PRCNCT":
            continue
        postal = row.category.endswith("_C")
        unit = b.unit(row, postal)
        f = row.fields
        if postal:
            accounting = {"cast": f.get("d"), "valid": f.get("d1"), "invalid": f.get("d2")}
        else:
            accounting = {
                "registered": f.get("a") or None,
                "cast": f.get("b"),
                "valid": f.get("c"),
                "invalid": f.get("d"),
            }
        b.record(contest, unit, accounting, postal)
        for line in row.lines:
            option = b.option(contest, str(line["candidate"]), None, True, position(line))
            votes = whole(line["votes"])
            if votes is not None:
                b.values.append((contest, unit, option, POSTAL if postal else 0, VOTES, votes))
    if runoff:
        p.artifact(raw.cached(SOURCE, BEC_RUNOFF), BEC_SOURCE)
        found = b.options.get((contest, ident(RUNOFF_WINNER, None)))
        if found is None:
            raise ValueError(f"{day}: the declared winner {RUNOFF_WINNER} has no ballot line")
        b.elected.append((contest, found[0]))
    b.stage()
    accept(p)
    if annulled:
        result_set = key("result_set", contest)
        p.add(
            "note",
            id=key("note", result_set, "annulled"),
            entity_id=result_set,
            kind="caveat",
            body_md=ANNULLED,
            lang="en",
        )
    return p, b


# Parliamentary elections


def parliament(parties: set[str]) -> tuple[Package, Build]:
    day, first, name = "2024-12-01", date(2024, 12, 1), "parlamentare01122024"
    p = package_of(
        f"{day}-general",
        "The Romanian parliamentary election of 1 December 2024 by polling station, for the Chamber of Deputies and "
        "the Senate, from the Permanent Electoral Authority's results portal",
    )
    p.artifact(raw.cached(SOURCE, URLS[name]), AEP_SOURCE)
    for url in prezenta_seats.URLS:
        p.artifact(raw.cached(SOURCE, url), BEC_SOURCE)
    eid = election(
        p,
        f"parliament-{day}",
        first,
        "general",
        "2024 Romanian parliamentary election",
        "Alegerile pentru Senat și Camera Deputaților din 01.12.2024",
        f"ro/{day}-general",
    )
    b = Build(p, day, parties)
    rows = records(name)
    circuits = {
        (row.station_county or ""): title(row.county_name if row.station_county == row.county else "")
        for row in rows
        if row.scope == "PRCNCT"
    }
    for row in rows:
        if row.scope != "PRCNCT":
            continue
        chamber = row.category.split("_")[0]
        postal = row.category.endswith("_C")
        circ = row.station_county or ""
        where = "Bucharest" if circ == BUCHAREST else "Romanians abroad" if circ == ABROAD else circuits[circ]
        where_ro = "București" if circ == BUCHAREST else "Diaspora" if circ == ABROAD else circuits[circ]
        english, romanian, slug, body = {
            "CD": ("Chamber of Deputies", "Camera Deputaților", "chamber", CHAMBER),
            "S": ("Senate", "Senat", "senate", SENATE),
        }[chamber]
        contest = f"{day}-{chamber}-{circ}"
        b.contest(
            Contest(
                contest,
                eid,
                f"2024 Romanian {english} election, {where}",
                f"Alegeri pentru {romanian}, {where_ro}, 2024",
                f"ro/{day}-general/{slug}/{circ}",
                "list_allocation",
                body=body,
            )
        )
        unit = b.unit(row, postal)
        f = row.fields
        if postal:
            accounting = {"cast": f.get("d"), "valid": f.get("d1"), "invalid": f.get("d2")}
        else:
            accounting = {
                "registered": f.get("a") or None,
                "cast": f.get("b"),
                "valid": f.get("e"),
                "invalid": f.get("f"),
                "blank": f.get("g"),
            }
        b.record(contest, unit, accounting, postal)
        for line in row.lines:
            label = str(line["candidate"])
            person = canon(line.get("party")) == INDEPENDENT
            option = b.option(contest, label, None, person, position(line), True if person else None)
            votes = whole(line["votes"])
            if votes is not None:
                b.values.append((contest, unit, option, POSTAL if postal else 0, VOTES, votes))
    allocate(b, day)
    b.stage()
    accept(p)
    p.accept(
        "accounting.ballots_sum",
        "some station records print valid, null, and blank votes that do not add up to the voters who took part, "
        "because voters left without casting a ballot; stored as printed",
    )
    return p, b


def allocate(b: Build, day: str) -> None:
    """The seats each list won in each constituency, and the seats each constituency fills, as the Central Electoral
    Bureau declared them."""
    for chamber in ("CD", "S"):
        for circ, seats in prezenta_seats.won(chamber).items():
            contest = f"{day}-{chamber}-{circ}"
            if contest not in b.contests:
                raise ValueError(f"{contest}: seats declared for a constituency without station records")
            for label, count in seats.items():
                found = b.options.get((contest, canon(label)))
                if found is None:
                    raise ValueError(f"{contest}: seats declared for {label} without a ballot line")
                b.seats[(contest, found[0])] = count
            b.contests[contest].magnitude = sum(seats.values())


# European Parliament


def european(parties: set[str]) -> tuple[Package, Build]:
    day, name = "2024-06-09", "europarlamentare09062024"
    p = package_of(
        f"{day}-european",
        "The election of Romania's members of the European Parliament of 9 June 2024 by polling station, from the "
        "Permanent Electoral Authority's results portal",
    )
    p.artifact(raw.cached(SOURCE, URLS[name]), AEP_SOURCE)
    eid = EU_RECORD[0]
    p.external |= {EU_RECORD[0], EU_RECORD[1], EU_RECORD[3]}
    b = Build(p, day, parties)
    contest = f"european-{day}"
    b.contest(
        Contest(
            contest,
            eid,
            "2024 European Parliament election in Romania",
            "Alegeri pentru Parlamentul European, România, 2024",
            f"ro/{day}-european/romania",
            "list_allocation",
            body=EUROPEAN_PARLIAMENT,
            magnitude=33,
            linked=EU_RECORD,
        )
    )
    for row in records(name):
        if row.scope != "PRCNCT":
            continue
        unit = b.unit(row)
        f = row.fields
        accounting = {"registered": f.get("a") or None, "cast": f.get("b"), "valid": f.get("e"), "invalid": f.get("f")}
        b.record(contest, unit, accounting, False)
        for line in row.lines:
            label = str(line["candidate"])
            person = is_person(label, parties)
            option = b.option(contest, label, None, person, independent=True if person else None)
            votes = whole(line["votes"])
            if votes is not None:
                b.values.append((contest, unit, option, 0, VOTES, votes))
    b.stage()
    accept(p)
    return p, b


# Local elections

LOCAL = {
    # report type prefix: (English noun, Romanian noun, slug, office or body, electoral system, whether a list vote)
    "CL": ("Local council", "Consiliul local", "local-council", LOCAL_COUNCIL, None, True),
    "P": ("Mayor", "Primar", "mayor", MAYOR, PLURALITY, False),
    "CJ": ("County council", "Consiliul județean", "county-council", COUNTY_COUNCIL, None, True),
    "PCJ": (
        "County council president",
        "Președintele consiliului județean",
        "county-president",
        COUNTY_PRESIDENT,
        PLURALITY,
        False,
    ),
    "CGMB": (
        "General Council of Bucharest",
        "Consiliul General al Municipiului București",
        "bucharest-council",
        BUCHAREST_COUNCIL,
        None,
        True,
    ),
    "PG": (
        "General Mayor of Bucharest",
        "Primarul General al Municipiului București",
        "general-mayor",
        GENERAL_MAYOR,
        PLURALITY,
        False,
    ),
}
LOCALS = {
    "2024-06-09": ("locale09062024v2", date(2024, 6, 9), "local", None),
    "2024-06-23": ("locale23062024", date(2024, 6, 23), "runoff", "2024-06-23"),
    "2024-07-07": ("locale07072024v2", date(2024, 7, 7), "by_election", "2024-07-07"),
    "2025-05-04": ("locale04052025", date(2025, 5, 4), "by_election", None),
    "2025-12-07": ("locale07122025", date(2025, 12, 7), "by_election", None),
}
LOCAL_NAMES = {
    "local": ("{year} Romanian local elections", "Alegeri locale din {date}"),
    "runoff": ("{year} Romanian local elections, second round", "Alegeri locale, turul al doilea, {date}"),
    "by_election": ("{year} Romanian partial local elections of {when}", "Alegeri locale parțiale din {date}"),
}
RUNOFF_NOTE = (
    "Where two mayoral candidates tied on 9 June 2024, the Authority held a further round between them on 23 June "
    "2024. The portal's files for that round also hold the first-round records of the rest of the county; only the "
    "records signed for the new round are stored here."
)
PARTIAL_NOTE = (
    "Partial local elections fill offices that fell vacant or whose election was held again. The portal's files for "
    "the election of 7 July 2024 also hold the records of 9 June 2024 for the rest of the county; only the records "
    "signed for the new election are stored here."
)


ELECTED_NOTE = (
    "The mayors and county council presidents declared elected are those with the most votes in the record of the "
    "constituency's or county's electoral bureau; where two candidates tied for the most votes, no one is declared "
    "elected in that round. Where the vote was held again on 7 July 2024, as in Costinești, the record of 9 June elects "
    "no one. The Central Electoral Bureau's national records of 2024 give the mandates won by each "
    "party, and the pipeline checks the declared mayors and county presidents against them."
)


def local_kind(row: Row) -> str:
    code = row.code.split("_")[0]
    return code if code in LOCAL else row.category


def local(day: str, parties: set[str]) -> tuple[Package, Build]:
    name, first, kind, _ = LOCALS[day]
    runoff = kind == "runoff"
    english, romanian = LOCAL_NAMES[kind]
    when = f"{first.day} {first.strftime('%B')}"
    stamp = f"{first.day:02d}.{first.month:02d}.{first.year}"
    p = package_of(
        f"{day}-local",
        f"The Romanian local elections of {first} by polling station, from the Permanent Electoral Authority's "
        "results portal",
        ["ro.prezenta/2024-06-09-local"] if runoff else [],
    )
    p.artifact(raw.cached(SOURCE, URLS[name]), AEP_SOURCE)
    eid = election(
        p,
        f"local-{day}",
        first,
        kind,
        english.format(year=first.year, when=when),
        romanian.format(date=stamp),
        f"ro/{day}-local" + ("-runoff" if runoff else ""),
    )
    if day in ("2024-06-23", "2024-07-07"):
        p.add(
            "note",
            id=key("note", eid, "scope"),
            entity_id=eid,
            kind="context",
            body_md=RUNOFF_NOTE if runoff else PARTIAL_NOTE,
            lang="en",
        )
    b = Build(p, day, parties)
    rows = signed(day)
    for row in rows:
        if row.scope != "PRCNCT":
            continue
        tip = local_kind(row)
        noun_en, noun_ro, slug, holder, system, listed = LOCAL[tip]
        county = row.county or ""
        scope = {"CL": "uat", "P": "uat", "CJ": "county", "PCJ": "county", "CGMB": "all", "PG": "all"}[tip]
        contest = local_contest(day, tip, county, row.uat)
        where = {
            "uat": title(row.uat_name),
            "county": f"{title(row.county_name)} County",
            "all": "",
        }[scope]
        where_ro = {"uat": title(row.uat_name), "county": title(row.county_name), "all": ""}[scope]
        slug_key = {"uat": row.uat, "county": county, "all": ""}[scope]
        b.contest(
            Contest(
                contest,
                eid,
                f"{first.year} Romanian local elections, {noun_en}"
                + (f", {where}" if where else "")
                + (", second round" if runoff else ""),
                f"{noun_ro}"
                + (f", {where_ro}" if where_ro else "")
                + (", turul al doilea" if runoff else "")
                + f", {stamp}",
                f"ro/{day}-local" + ("-runoff" if runoff else "") + f"/{slug}" + (f"/{slug_key}" if slug_key else ""),
                "list_allocation" if listed else "office",
                body=holder if listed else None,
                office=None if listed else holder,
                magnitude=None if listed else 1,
                system=system,
                round_number=2 if runoff else 1,
                previous=key("contest", local_contest("2024-06-09", tip, county, row.uat)) if runoff else None,
                method=LOCAL_METHOD,
            )
        )
        unit = b.unit(row)
        f = row.fields
        accounting = {"registered": f.get("a") or None, "cast": f.get("b"), "valid": f.get("c"), "invalid": f.get("d")}
        b.record(contest, unit, accounting, False)
        for line in row.lines:
            label = str(line["candidate"])
            person = not listed or is_person(label, parties)
            nominated = None if listed else nominator(line)
            option = b.option(contest, label, nominated, person, independent=None if not person else nominated is None)
            votes = whole(line["votes"])
            if votes is not None:
                b.values.append((contest, unit, option, 0, VOTES, votes))
    declare(b, [r for r in rows if r.scope in ("UAT", "CNTY")], day)
    if day == "2024-06-09":
        # a vote held again on 7 July 2024 elected no one on 9 June, whatever the bureau's record of that day shows
        again = {local_contest(day, local_kind(r), r.county or "", r.uat) for r in signed("2024-07-07")}
        b.elected = [(contest, option) for contest, option in b.elected if contest not in again]
    if b.elected:
        for url in BEC_LOCAL:
            p.artifact(raw.cached(SOURCE, url), BEC_SOURCE)
        p.add("note", id=key("note", eid, "elected"), entity_id=eid, kind="context", body_md=ELECTED_NOTE, lang="en")
    if day == "2024-06-09":
        check_presidents(b)
    b.stage()
    accept(p)
    return p, b


def signed(day: str) -> list[Row]:
    """The records of one local election; for a runoff or a partial election, only those signed for it."""
    name, _, _, cutoff = LOCALS[day]
    rows = records(name)
    if cutoff:
        fresh = {(r.county, r.uat) for r in rows if r.scope == "UAT" and r.created[:10] >= cutoff}
        rows = [
            r for r in rows if r.scope in ("PRCNCT", "UAT") and (r.county, r.uat) in fresh and r.created[:10] >= cutoff
        ]
        if not rows:
            raise ValueError(f"{day}: no records signed for the election")
    return rows


def local_contest(day: str, tip: str, county: str, uat: str | None) -> str:
    if tip in ("CL", "P"):
        return f"{day}-{tip}-{county}-{uat}"
    if tip in ("CJ", "PCJ"):
        return f"{day}-{tip}-{county}"
    return f"{day}-{tip}"


def declare(b: Build, rows: list[Row], day: str) -> None:
    """The totals and seats of each constituency and county bureau, as the portal prints them."""
    for row in rows:
        tip = local_kind(row)
        if tip not in LOCAL:
            continue
        if row.scope == "UAT" and tip not in ("CL", "P"):
            continue
        if row.scope == "CNTY" and tip not in ("CJ", "PCJ", "CGMB", "PG"):
            continue
        county = row.county or ""
        contest = local_contest(day, tip, county, row.uat)
        if contest not in b.contests:
            raise ValueError(f"{day}: a bureau record for {contest} without station records")
        listed = LOCAL[tip][5]
        f = row.fields
        for measure, value in (
            (REGISTERED, f.get("a")),
            (CAST, f.get("b")),
            (VALID, f.get("c")),
            (INVALID, f.get("d")),
        ):
            if value is not None:
                b.declared.append((contest, None, measure, value))
        total = 0
        for line in row.lines:
            label = str(line["candidate"])
            found = b.options.get((contest, ident(label, None if listed else nominator(line))))
            votes = whole(line["votes"])
            if found is None:
                if votes:
                    raise ValueError(f"{day}: {contest} declares votes for {label} without a ballot line")
                continue
            if votes is not None:
                b.declared.append((contest, found[0], VOTES, votes))
            if listed:
                seats = sum(whole(line.get(k)) or 0 for k in line if str(k).startswith("mandates"))
                if seats:
                    b.seats[(contest, found[0])] = seats
                    total += seats
        if listed and total:
            b.contests[contest].magnitude = total
        if not listed:
            best = [(whole(line["votes"]) or 0, str(line["candidate"]), nominator(line)) for line in row.lines]
            top = max((votes for votes, _, _ in best), default=0)
            leaders = [(label, party) for votes, label, party in best if votes == top and top > 0]
            if len(leaders) == 1:
                found = b.options.get((contest, ident(*leaders[0])))
                if found is not None:
                    b.elected.append((contest, found[0]))


def check_presidents(b: Build) -> None:
    """The county council presidents elected on 9 June 2024 add up to the mandates of the Bureau's national record."""
    party = {found[0]: found[2] for found in b.options.values()}
    won: dict[str, int] = {}
    for contest, option in b.elected:
        if b.contests[contest].office == COUNTY_PRESIDENT:
            name = canon(party.get(option) or INDEPENDENT)
            won[name] = won.get(name, 0) + 1
    expected = {canon(name): n for name, n in PRESIDENT_MANDATES.items()}
    if won != expected:
        raise ValueError(f"county council presidents elected by party {won} differ from the Bureau's {expected}")


# Registry


def registry(labels: set[str], people: dict[str, str]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Romanian parties, alliances, minority organizations, and presidential candidates on the ballots of 2024 and "
        "2025, with the offices, "
        "bodies, and electoral systems they contest, from the Permanent Electoral Authority's results portal",
    )
    p.external.add(ROMANIA)
    p.add(
        "source",
        id=AEP_SOURCE,
        kind="official_body",
        publisher="Autoritatea Electorală Permanentă",
        title="Results portal of the Permanent Electoral Authority (prezenta.roaep.ro)",
        url=SITE,
        attribution="Autoritatea Electorală Permanentă",
    )
    p.add(
        "source",
        id=BEC_SOURCE,
        kind="official_body",
        publisher="Biroul Electoral Central",
        title="Final results published by the Central Electoral Bureau (bec.ro)",
        url="https://www.bec.ro",
        attribution="Biroul Electoral Central",
    )
    for body, kind, english, romanian in (
        (CHAMBER, "lower_house", "Chamber of Deputies of Romania", "Camera Deputaților"),
        (SENATE, "upper_house", "Senate of Romania", "Senatul României"),
        (EUROPEAN_PARLIAMENT, "unicameral", "European Parliament", "Parlamentul European"),
        (LOCAL_COUNCIL, "council", "Local councils of Romania", "Consiliile locale"),
        (COUNTY_COUNCIL, "council", "County councils of Romania", "Consiliile județene"),
        (BUCHAREST_COUNCIL, "council", "General Council of Bucharest", "Consiliul General al Municipiului București"),
    ):
        p.add("body", id=body, polity_id=ROMANIA, kind=kind)
        p.name(body, english, "en", "official")
        p.name(body, romanian, "ro", "native", False)
    for office, kind, english, romanian in (
        (PRESIDENT, "head_of_state", "President of Romania", "Președintele României"),
        (MAYOR, "mayor", "Mayor of a Romanian municipality, town, or commune", "Primar"),
        (GENERAL_MAYOR, "mayor", "General Mayor of Bucharest", "Primarul General al Municipiului București"),
        (COUNTY_PRESIDENT, "other", "President of a county council", "Președintele consiliului județean"),
    ):
        p.add("office", id=office, polity_id=ROMANIA, kind=kind)
        p.name(office, english, "en", "official")
        p.name(office, romanian, "ro", "native", False)
    p.add("electoral_system", id=PLURALITY, polity_id=ROMANIA, rules={"formula": "fptp"}, rules_version=1)
    p.add(
        "note",
        id=key("note", PLURALITY),
        entity_id=PLURALITY,
        kind="methodology",
        body_md="The candidate with the most votes in a single round is elected. Where two candidates tie, a further "
        "round is held between them.",
        lang="en",
    )
    p.add(
        "electoral_system",
        id=TWO_ROUND,
        polity_id=ROMANIA,
        rules={"formula": "two_round", "majority": 0.5, "advance": 2},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", TWO_ROUND),
        entity_id=TWO_ROUND,
        kind="methodology",
        body_md="A candidate who wins a majority of the registered voters in the first round is elected; otherwise the "
        "two leading candidates meet in a second round, which the candidate with the most votes wins.",
        lang="en",
    )
    for who, text in sorted(people.items(), key=lambda item: item[1]):
        p.add("person", id=who)
        p.name(who, text, "ro", "official")
    seen: dict[str, str] = {}
    for label in sorted(labels):
        seen.setdefault(canon(label), label)
    for text, label in sorted(seen.items()):
        id = party_id(label)
        p.add("party", id=id, kind="bloc" if text.startswith("ALIAN") else "party", polity_id=ROMANIA)
        p.name(id, label, "ro", "official")
    return p


def known_parties() -> set[str]:
    """Names that are parties: those that nominate a candidate, the lists on the parliamentary ballot, and the lists
    the county and national bureaus sum by party."""
    found: set[str] = set()
    for name in ("locale09062024v2", "parlamentare01122024", "locale04052025", "locale07122025"):
        for row in records(name):
            for line in row.lines:
                party = nominator(line)
                if party:
                    found.add(canon(party))
                if name.startswith("parlamentare") and not line.get("party"):
                    found.add(canon(line["candidate"]))
                # the national bureau, and the county bureaus for local councils, sum each party's lists and print
                # every independent as one line
                if (row.scope, local_kind(row)) in (
                    ("CNTRY", "CL"),
                    ("CNTRY", "CJ"),
                    ("CNTRY", "CGMB"),
                    ("CNTY", "CL"),
                ):
                    found.add(canon(line["candidate"]))
    found.discard(INDEPENDENT)
    return found


ELECTIONS = [
    "2024-06-09-european",
    "2024-06-09-local",
    "2024-06-23-local",
    "2024-07-07-local",
    "2024-11-24-presidential",
    "2024-12-01-general",
    "2025-05-04-presidential",
    "2025-05-04-local",
    "2025-05-18-presidential",
    "2025-12-07-local",
]


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/{name}" for name in ELECTIONS)]


def build(name: str, parties: set[str]) -> tuple[Package, Build]:
    day, kind = name[:10], name[11:]
    if kind == "european":
        return european(parties)
    if kind == "general":
        return parliament(parties)
    if kind == "presidential":
        return presidential(day, parties)
    return local(day, parties)


def parse() -> None:
    parties = known_parties()
    labels: set[str] = set()
    people: dict[str, str] = {}
    mayors = 0
    for name in ELECTIONS:
        p, b = build(name, parties)
        p.write()
        labels |= b.labels
        people |= b.shared
        if name[:10] in ("2024-06-09", "2024-06-23", "2024-07-07") and name.endswith("-local"):
            mayors += sum(1 for c, _ in b.elected if b.contests[c].office == MAYOR)
    if mayors != MAYOR_MANDATES:
        raise ValueError(f"{mayors} mayors declared elected in 2024, the Bureau's record has {MAYOR_MANDATES}")
    registry(labels, people).write()


def emit() -> None:
    package.run("load", packages())
