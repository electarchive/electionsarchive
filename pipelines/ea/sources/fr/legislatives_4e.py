"""France's Constituent Assembly and National Assembly elections of the Fourth Republic, 1945 to 1956.

The source is the commune-level digitization of the electoral records kept at the Archives nationales (boxes
C//10058 onward), published by Julia Cagé and Thomas Piketty. Each commune row carries its registered voters,
voters, valid votes, and the votes of each party family. This pipeline sums the communes of each old département,
so a contest is one département and its reporting unit is the département the Interior Ministry counted.
"""

import csv
import io
import re
import zipfile
from collections import Counter, defaultdict
from datetime import date
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import cast
from uuid import uuid5

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "fr.legislatives_4e"
BASE = "https://conflit-politique-data.ams3.cdn.digitaloceanspaces.com/zip/"
FRANCE = "01a13b8a-b1bc-7045-aa86-d470ebdf31b2"
NATION = "01a13b8a-b1cf-7371-8194-5f2639c265b0"
UNITS = Path(__file__).with_name("legislatives_4e_units.tsv")
REGISTRY = "fr.legislatives_4e/registry"

# Each election: the archive file, the table inside it, the day, the body it filled, and the kind of its system.
ELECTIONS: dict[str, dict[str, object]] = {
    "1945": {
        "zip": "leg1945_csv.zip",
        "member": "leg1945_csv/leg1945comm.csv",
        "day": date(1945, 10, 21),
        "body": "constituent-1945",
        "en": "October 1945 French Constituent Assembly election",
        "fr": "Élection de la première Assemblée nationale constituante, octobre 1945",
        "seats": 586,
        "system": (
            "Each département elected its members of the first Constituent Assembly from closed party lists in a "
            "single round, by proportional representation. Seats were allocated among the lists within the "
            "département. Voters could not alter a list."
        ),
    },
    "1946-06": {
        "zip": "leg1946Juin_csv.zip",
        "member": "leg1946Juin_csv/leg1946comm.csv",
        "day": date(1946, 6, 2),
        "body": "constituent-1946",
        "en": "June 1946 French Constituent Assembly election",
        "fr": "Élection de la seconde Assemblée nationale constituante, juin 1946",
        "seats": 586,
        "system": (
            "Each département elected its members of the second Constituent Assembly from closed party lists in a "
            "single round, by proportional representation."
        ),
    },
    "1946-11": {
        "zip": "leg1946Nov_csv.zip",
        "member": "leg1946Nov_csv/leg1947comm.csv",
        "day": date(1946, 11, 10),
        "body": "assembly",
        "en": "November 1946 French National Assembly election",
        "fr": "Élections législatives de novembre 1946",
        "seats": 619,
        "system": (
            "Each département elected its deputies to the first National Assembly of the Fourth Republic from closed "
            "party lists in a single round, by proportional representation."
        ),
    },
    "1951": {
        "zip": "leg1951_csv.zip",
        "member": "leg1951_csv/leg1951comm.csv",
        "day": date(1951, 6, 17),
        "body": "assembly",
        "en": "1951 French legislative election",
        "fr": "Élections législatives de 1951",
        "seats": 627,
        "system": (
            "Each département elected its deputies from closed party lists in a single round. Lists could declare "
            "an apparentement (an alliance); an apparentement that won an absolute majority of the valid votes in the "
            "département took all its seats, and otherwise the lists divided the seats by proportional representation. "
            "The source gives each list's own votes, not the votes of its apparentement."
        ),
    },
    "1956": {
        "zip": "leg1956_csv.zip",
        "member": "leg1956_csv/leg1956comm.csv",
        "day": date(1956, 1, 2),
        "body": "assembly",
        "en": "1956 French legislative election",
        "fr": "Élections législatives de 1956",
        "seats": 597,
        "system": (
            "Each département elected its deputies from closed party lists in a single round, with apparentements "
            "as in 1951. The source gives each list's own votes, not the votes of its apparentement. Algeria did not "
            "vote, by a decree of 12 December 1955."
        ),
    },
}
NEXT_DAY = {"1945": date(1946, 6, 2), "1946-06": date(1946, 10, 27), "1946-11": date(1951, 6, 17)}
BODIES = {
    "constituent-1945": (
        "constituent_assembly",
        "First Constituent Assembly",
        "Première Assemblée nationale constituante",
    ),
    "constituent-1946": (
        "constituent_assembly",
        "Second Constituent Assembly",
        "Seconde Assemblée nationale constituante",
    ),
    "assembly": ("lower_house", "National Assembly (Fourth Republic)", "Assemblée nationale (IVe République)"),
}
# The source's own party-family columns. A family it names that the archive can place on a known party has a party row;
# the others are stored as the source's own category.
FAMILIES: dict[str, tuple[str, str | None]] = {
    "PCF": ("Parti communiste français", "PCF"),
    "PCI": ("Parti communiste internationaliste", "PCI"),
    "SFIO": ("Section française de l'Internationale ouvrière", "SFIO"),
    "MRP": ("Mouvement républicain populaire", "MRP"),
    "UDSR": ("Union démocratique et socialiste de la Résistance", "UDSR"),
    "PRL": ("Parti républicain de la liberté", "PRL"),
    "RPF": ("Rassemblement du peuple français", "RPF"),
    "RADSOC": ("Parti républicain radical et radical-socialiste", "Radical"),
    "RGR": ("Rassemblement des gauches républicaines", "RGR"),
}
LABELS = {
    "PCFSFIO": "Joint PCF–SFIO lists",
    "DVG": "Divers gauche",
    "DVD": "Divers droite",
    "DIV": "Divers",
}
OLD_NAMES = {
    "04": "Basses-Alpes",
    "20": "Corse",
    "22": "Côtes-du-Nord",
    "44": "Loire-Inférieure",
    "64": "Basses-Pyrénées",
    "75": "Seine",
    "76": "Seine-Inférieure",
    "78": "Seine-et-Oise",
}
METHOD = (
    "The votes are the sums of the communes of the département in the Cagé–Piketty digitization of the electoral "
    "records at the Archives nationales. The source groups lists by party family, so an option is a family, not a "
    "single list, and a family with no votes in the département is left out. Registered voters, voters, and valid "
    "votes come from the same rows. Overseas territories and Algeria are not in the source. Each figure is the "
    "commune sum rounded to a whole vote."
)
OLD_CODES = (
    " The 1956 file holds about 30,500 communes against about 36,000 in the other years, and its national totals fall "
    "short of the published ones, so its counts are incomplete in some départements. The file does not name each commune's département at the time, so each commune takes the département the "
    "1951 file gives its INSEE code, and a commune absent from it takes its current département, with Corsica, the "
    "Seine, and Seine-et-Oise grouped as they were."
)
FRACTIONS = (
    " The source prints fractional votes for some communes, which it estimated by sharing the département's list "
    "totals among its communes."
)


def url(name: str) -> str:
    return BASE + name


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


CAGE_SOURCE = key("source", "cage_piketty")


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{name}" for name in ELECTIONS)]


def fetch() -> None:
    for election in ELECTIONS.values():
        raw.fetch(SOURCE, url(str(election["zip"])), suffix=".zip")


def parse() -> None:
    registry().write()
    for name in ELECTIONS:
        election(name).write()


def emit() -> None:
    package.run("load", packages())


def _units() -> dict[str, tuple[str, str, str]]:
    with UNITS.open(encoding="utf-8") as handle:
        return {
            row["code"]: (row["unit_id"], row["area_id"], row["area_name"])
            for row in csv.DictReader(handle, delimiter="\t")
        }


def party(family: str) -> str:
    return key("party", family)


def _family_columns(header: list[str]) -> list[str]:
    return [
        column[4:] for column in header if column.startswith("voix") and not column.startswith(("voixtot", "voixratio"))
    ]


def _rows(name: str) -> list[dict[str, str]]:
    spec = ELECTIONS[name]
    path = raw.cached(SOURCE, url(str(spec["zip"]))).path
    with zipfile.ZipFile(path) as archive:
        text = archive.read(str(spec["member"])).decode("utf-8", errors="replace")
    return list(csv.DictReader(io.StringIO(text)))


def _normal(text: str) -> str:
    return re.sub(r"[^A-Z]", "", text.upper().replace("Ô", "O").replace("É", "E"))


FOLD = {"2A": "20", "2B": "20", "92": "75", "93": "75", "94": "75", "91": "78", "95": "78"}
LEDGER = "1951"


def _codes(rows: list[dict[str, str]], units: dict[str, tuple[str, str, str]]) -> dict[str, str]:
    """The code of each commune's old département.

    Most files name the old département of each commune. The 1956 file does not, so its communes take the département
    the 1951 file gives their INSEE code, and the few it lacks take their current département, with Corsica, the Seine,
    and Seine-et-Oise folded into the old départements they came from.
    """
    codes: dict[str, str] = {}
    if "nomdepancien" in rows[0]:
        seen: dict[str, Counter[str]] = defaultdict(Counter)
        for row in rows:
            seen[row["nomdepancien"]][row["dep"]] += 1
        named: dict[str, str] = {}
        for old, counts in seen.items():
            norm = _normal(old)
            code = {"SEINE": "75", "SEINEETOISE": "78", "CORSE": "20"}.get(norm) or counts.most_common(1)[0][0]
            if code not in units:
                raise ValueError(f"{old} maps to {code}, which has no reporting unit")
            named[old] = code
        if len(set(named.values())) != len(named):
            raise ValueError(f"two old départements map to one code: {sorted(named.items())}")
        return {row["codecommune"]: named[row["nomdepancien"]] for row in rows}
    earlier = _codes(_rows(LEDGER), units)
    for row in rows:
        code = earlier.get(row["codecommune"]) or FOLD.get(row["dep"], row["dep"])
        if code not in units:
            raise ValueError(f"{row['codecommune']} maps to {code}, which has no reporting unit")
        codes[row["codecommune"]] = code
    return codes


def _whole(value: Decimal) -> int:
    return int(value.quantize(Decimal(1), rounding=ROUND_HALF_UP))


def _apportion(values: list[Decimal], total: int) -> list[int]:
    """Round each value down, then give the remaining votes to the largest remainders, so the parts add to the total."""
    floors = [int(value) for value in values]
    short = total - sum(floors)
    order = sorted(range(len(values)), key=lambda i: values[i] - floors[i], reverse=True)
    for i in order[: max(short, 0)]:
        floors[i] += 1
    return floors


def _decimal(text: str | None) -> Decimal:
    return Decimal(text) if text not in (None, "", "NA") else Decimal(0)


def totals(name: str) -> dict[str, dict[str, Decimal]]:
    """Sums by old département code: registered, voters, valid, and each family's votes."""
    rows = _rows(name)
    units = _units()
    codes = _codes(rows, units)
    families = _family_columns(list(rows[0]))
    out: dict[str, dict[str, Decimal]] = defaultdict(lambda: defaultdict(Decimal))
    seats: dict[str, set[Decimal]] = defaultdict(set)
    for row in rows:
        code = codes[row["codecommune"]]
        out[code]["registered"] += _decimal(row["inscrits"])
        out[code]["voters"] += _decimal(row["votants"])
        out[code]["valid"] += _decimal(row["exprimes"])
        for family in families:
            out[code][family] += _decimal(row[f"voix{family}"])
        if row.get("nb_deputes_dpt") not in (None, "", "NA"):
            seats[code].add(_decimal(row["nb_deputes_dpt"]))
    for code, values in seats.items():
        if len(values) == 1:
            out[code]["magnitude"] = next(iter(values))
        else:
            out[code]["split"] = Decimal(len(values))
    return out


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "Fourth Republic assemblies and the party families of their elections, 1945 to 1956",
    )
    p.external |= {FRANCE}
    p.add(
        "source",
        id=CAGE_SOURCE,
        kind="academic",
        publisher="J. Cagé and T. Piketty",
        title="Une histoire du conflit politique: élections et inégalités sociales en France, 1789-2022 (commune-level "
        "election data)",
        url="https://unehistoireduconflitpolitique.fr/",
        license="Free reuse with the source cited (Cagé and Piketty 2023)",
        license_url="https://unehistoireduconflitpolitique.fr/",
        attribution="J. Cagé and T. Piketty (2023), Une histoire du conflit politique, Seuil",
        notes="Digitized from the electoral records kept at the Archives nationales (boxes C//10058 to C//10062 for "
        "1945).",
    )
    for spec in ELECTIONS.values():
        p.artifact(raw.cached(SOURCE, url(str(spec["zip"]))), CAGE_SOURCE)
    for slug_, (kind, en, fr) in BODIES.items():
        body = key("body", slug_)
        p.add("body", id=body, polity_id=FRANCE, kind=kind)
        p.name(body, en, "en", "official")
        p.name(body, fr, "fr", "native", False)
    spans: dict[str, list[tuple[date, date | None, int]]] = defaultdict(list)
    for name, spec in ELECTIONS.items():
        spans[str(spec["body"])].append((cast(date, spec["day"]), NEXT_DAY.get(name), cast(int, spec["seats"])))
    for slug_, rows in spans.items():
        for since, until, seats in rows:
            p.add("body_size", body_id=key("body", slug_), seats=seats, valid_from=since, valid_to=until)
    used: set[str] = set()
    for name in ELECTIONS:
        used |= set(_family_columns(list(_rows(name)[0])))
    for family in sorted(used):
        if family not in FAMILIES:
            continue
        full, short = FAMILIES[family]
        id = party(family)
        p.add("party", id=id, kind="party", polity_id=FRANCE)
        p.name(id, full, "fr", "official")
        if short:
            p.name(id, short, "fr", "abbreviation", False)
    return p


def _label(family: str) -> str:
    if family in FAMILIES:
        return FAMILIES[family][0]
    return LABELS.get(family, f"Source category {family}")


def election(name: str) -> Package:
    spec = ELECTIONS[name]
    day = cast(date, spec["day"])
    p = Package(
        f"{SOURCE}/{name}",
        SOURCE,
        f"The {day.isoformat()} French election to the {BODIES[str(spec['body'])][1]} by département",
        depends=[REGISTRY],
    )
    units = _units()
    p.external |= {FRANCE, NATION}
    p.uses(raw.cached(SOURCE, url(str(spec["zip"]))))
    sums = totals(name)
    earlier = totals(LEDGER) if name == "1956" else {}
    families = _family_columns(list(_rows(name)[0]))
    body = key("body", str(spec["body"]))
    ev = key("election", name)
    p.add("election", id=ev, polity_id=FRANCE, kind="general", date_from=day, date_to=day, status="held")
    p.name(ev, str(spec["en"]), "en", "official")
    p.name(ev, str(spec["fr"]), "fr", "native", False)
    path = f"fr/{day.isoformat()}-general"
    p.add("slug", path=path, entity_id=ev, is_current=True)
    p.add("note", id=key("note", ev), entity_id=ev, kind="methodology", body_md=str(spec["system"]), lang="en")
    fractional = any(
        value != value.to_integral_value()
        for figures in sums.values()
        for column, value in figures.items()
        if column != "magnitude"
    )
    method = METHOD + (FRACTIONS if fractional else "") + (OLD_CODES if name == "1956" else "")
    for code in sorted(sums, key=lambda value: (len(value), value)):
        unit, area, area_name = units[code]
        label = OLD_NAMES.get(code, area_name)
        figures = sums[code]
        contest = key("contest", name, code)
        p.external |= {unit, area}
        magnitude = int(figures["magnitude"]) if figures.get("magnitude") else None
        p.add(
            "contest",
            id=contest,
            election_id=ev,
            kind="list_allocation",
            body_id=body,
            district_area_id=area,
            magnitude=magnitude,
            round_number=1,
            default_result_kind="counted",
        )
        p.name(contest, label, "fr", "official")
        p.add("slug", path=f"{path}/{changeset.slug(label)}", entity_id=contest, is_current=True)
        result_set = key("result_set", name, code)
        p.add(
            "result_set",
            id=result_set,
            election_id=ev,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=CAGE_SOURCE,
            status="active",
            is_default=True,
            completeness=None if name == "1956" else 1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=method, lang="en")
        valid = _whole(figures["valid"])
        voters = _whole(figures["voters"])
        notes = []
        if voters < valid:
            notes.append(
                f"The source gives {voters:,} voters against {valid:,} valid votes, which cannot both hold, so the "
                "voters are not stored."
            )
        before = earlier.get(code, {}).get("registered")
        short = name == "1956" and before and figures["registered"] < Decimal("0.9") * before
        if short:
            notes.append(
                f"The source lists {_whole(figures['registered']):,} registered voters here against "
                f"{_whole(before):,} in 1951, so communes of this département are probably missing from it and the "
                "counts are lower than the official ones."
            )
        if figures.get("split"):
            notes.append(
                f"The source assigns the communes of this département to {int(figures['split'])} different "
                "constituency sizes, so its lists competed in several constituencies; the archive sums them into one "
                "contest and leaves the number of seats out."
            )
        for n, text in enumerate(notes):
            p.add("note", id=key("note", result_set, n), entity_id=result_set, kind="caveat", body_md=text, lang="en")
        for measure, column in ((2, "registered"), (3, "voters"), (4, "valid")):
            if measure == 3 and voters < valid:
                continue
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=None,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=_whole(figures[column]),
            )
        present = [family for family in families if _whole(figures[family]) > 0]
        total = sum((figures[family] for family in present), Decimal(0))
        counted = dict(
            zip(
                present,
                _apportion(
                    [figures[family] for family in present],
                    valid if abs(total - figures["valid"]) < 1 else _whole(total),
                ),
                strict=True,
            )
        )
        for position, family in enumerate(families, start=1):
            if family not in counted:
                continue
            votes = counted[family]
            option = key("option", name, code, family)
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind="list",
                party_id=party(family) if family in FAMILIES else None,
                ballot_position=position,
                ballot_label=_label(family),
            )
            p.add(
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
    return p
