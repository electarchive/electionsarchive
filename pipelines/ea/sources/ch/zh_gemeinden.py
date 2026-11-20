"""Elections of the communal presidents (Gemeindepräsident, Stadtpräsident) in the canton of Zürich.

The Statistical Office of the canton publishes the results of the communal authority elections of 2022 and 2026 as
two open data files with one row per candidate and election. The president of the Gemeinderat or Stadtrat is elected
by the voters in a majority vote of its own, in a first ballot and, when nobody has the absolute majority, a second
ballot. The files do not mark an election's body, so the presidencies of the executive are picked from the
descriptions the communes typed in, and the presidencies of school boards and audit commissions are left out.
"""

import csv
import re
from collections import defaultdict
from datetime import date
from typing import NamedTuple
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.changeset import slug
from ea.package import Package
from ea.store import DATA

SOURCE = "ch.zh_gemeinden"
FILES = {
    2022: "https://daten.statistik.zh.ch/ogd/daten/ressourcen/KTZH_00001281_00002419.csv",
    2026: "https://daten.statistik.zh.ch/ogd/daten/ressourcen/KTZH_00003165_00006820.csv",
}
PAGES = {
    2022: "https://opendata.swiss/de/dataset/wahlresultate-gemeindebehordenwahlen-im-kanton-zurich-2022",
    2026: "https://opendata.swiss/de/dataset/archiv-wahlresultate-gemeindebehordenwahlen-im-kanton-zurich",
}
CH = "01a13b73-f75e-7177-8281-2867f60d0ee8"
CH_ZH = "01a13b73-f774-709b-aa70-36303f6a2fb5"
CH_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1fzh"))
SCHEME = "ch.communal_electorate"
SYSTEM = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1felectoral_system\x1fcommunal_president"))
REFERENCE = DATA / "reference" / "ch"
METHOD = (
    "Counted by each commune and published by the Statistical Office of the canton of Zürich. The president of the "
    "communal executive is elected by the voters in a majority vote. A candidate who reaches the absolute majority "
    "in the first ballot is elected; otherwise a second ballot follows, open to any eligible person, and the "
    "candidate with the most votes is elected. Registered voters are those entitled to vote, ballots cast are the "
    "ballots handed in, and valid ballots are those with at least one valid line or blank line. Blank and invalid "
    "lines on valid ballots are counted by the commune but are not an option of the contest, so the candidates' "
    "votes, with the scattered votes (Vereinzelte), fall short of the valid ballots by those lines."
)
EXCLUDED = re.compile(
    r"schul|rechnungs|rpk|gpk|geschäftsprüf|sozial|kirch|verband|bau|vormund|werk|primar|sekundar|ksb|kreis|pflege"
    r"|fürsorge|betrieb|kommission|behörde|synode|friedens|steuer|wald|spital|zivil|vize",
    re.IGNORECASE,
)
INCLUDED = re.compile(r"gemeindepr|stadtpr", re.IGNORECASE)
PRESIDENT = re.compile(r"pr[äa]sid", re.IGNORECASE)
INDEPENDENT = {"", "na", "parteilos", "partelos", "unabhängig", "keine", "ohne", "ohne partei", "-"}
SCATTERED = "vereinzelte"


class Circle(NamedTuple):
    code: str
    name: str
    registered: int | None
    cast: int | None
    invalid: int | None
    blank: int | None
    valid: int | None
    votes: dict[str, int | None]
    lines: int | None


class Candidate(NamedTuple):
    key: str
    first: str
    last: str
    party: str
    party_name: str
    female: bool | None
    incumbent: bool
    elected: bool
    scattered: bool


class Round(NamedTuple):
    bfs: str
    name: str
    day: date
    description: str
    majority: int | None
    candidates: list[Candidate]
    circles: list[Circle]


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fetch() -> None:
    for url in FILES.values():
        raw.fetch(SOURCE, url, suffix=".csv")


def _table(year: int) -> list[dict[str, str]]:
    path = raw.cached(SOURCE, FILES[year]).path
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def _number(text: str | None) -> int | None:
    if text is None or text in {"", "NA"}:
        return None
    return int(float(text))


def _text(text: str | None) -> str:
    return "" if text in {None, "NA"} else " ".join(str(text).split())


def _is_president(text: str, parent: str = "", main: bool = False) -> bool:
    """Whether a description names the presidency of the communal executive. A bare "Präsidium" takes its body from
    the election it belongs to (`parent`), and the president of a council that also elects members at the same
    ballot (`main`) is not told apart from the members' own election."""
    if not PRESIDENT.search(text) or (main and re.search(r"mitglied", text, re.IGNORECASE)):
        return False
    if INCLUDED.search(text):
        return True
    if EXCLUDED.search(text):
        return False
    return bool(re.search(r"gemeinde|stadt", text, re.IGNORECASE)) or not EXCLUDED.search(parent)


def _rounds_2026() -> list[Round]:
    groups: dict[str, list[dict[str, str]]] = defaultdict(list)
    for row in _table(2026):
        if row["electionIdentification"] and row["pollingDay"] and row["election_numberOfMandates"] == "1":
            groups[row["electionIdentification"]].append(row)
    out: list[Round] = []
    for rows in groups.values():
        first = rows[0]
        description = _text(first["electionDescriptionInfo-de_electionDescription"])
        if not _is_president(description, _text(first["electionDescriptionInfo-de_electionDescriptionShort"])):
            continue
        candidates: dict[str, Candidate] = {}
        circles: dict[str, dict[str, str]] = {}
        votes: dict[str, dict[str, int | None]] = defaultdict(dict)
        scattered: dict[str, int | None] = {}
        for row in rows:
            circle = row["countingCircle_countingCircleId"]
            circles[circle] = row
            scattered[circle] = _number(row["countOfIndividualVotesTotal"])
            ident = f"{row['candidate_familyName']}|{row['candidate_firstName']}|{row['candidate_yearOfBirth']}"
            candidates.setdefault(
                ident,
                Candidate(
                    ident,
                    _text(row["candidate_politicalFirstName"] or row["candidate_firstName"]),
                    _text(row["candidate_politicalFamilyName"] or row["candidate_familyName"]),
                    _text(row["partyAffiliationInfo-de_partyAffiliationShort"]),
                    _text(row["partyAffiliationInfo-de_partyAffiliationLong"]),
                    {"W": True, "M": False}.get(row["candidate_sex"]),
                    row["candidate_incumbentYesNo"] == "1",
                    row["elected"] == "1",
                    False,
                ),
            )
            votes[circle][ident] = _number(row["countOfVotesTotal"])
        scatter = Candidate(SCATTERED, "", "Vereinzelte", "", "", None, False, False, True)
        flat: list[Circle] = []
        for circle, row in circles.items():
            share = dict(votes[circle])
            share[SCATTERED] = scattered[circle]
            flat.append(
                Circle(
                    circle,
                    _text(row["countingCircle_countingCircleName"]),
                    _number(row["countOfVotersInformation_countOfVotersTotal"]),
                    _number(row["resultData_countOfReceivedBallots"]),
                    _number(row["resultData_countOfInvalidBallots"]),
                    _number(row["resultData_countOfBlankBallots"]),
                    _number(row["resultData_countOfValidBallots"]),
                    share,
                    (_number(row["countOfBlankVotesTotal"]) or 0) + (_number(row["countOfInvalidVotesTotal"]) or 0),
                )
            )
        out.append(
            Round(
                f"{int(first['superiorAuthority_domainOfInfluenceIdentification']):04d}",
                _text(first["superiorAuthority_domainOfInfluenceName"]),
                date.fromisoformat(first["pollingDay"]),
                description,
                _number(first["absoluteMajority"]),
                [*candidates.values(), scatter],
                flat,
            )
        )
    return out


def _rounds_2022() -> list[Round]:
    groups: dict[tuple[str, str, str, str], list[dict[str, str]]] = defaultdict(list)
    for row in _table(2022):
        if (
            row["GeArt"] != "Gde"
            or row["Sonntag"] in {"", "NA"}
            or not re.fullmatch(r"ZH(\d+(K\d+)?|ZP\d+)|NA", row["EinheitCode"])
        ):
            continue
        for suffix, bit in (("NW", 2), ("NW2", 4), ("", 1)):
            mandates = _number(row[f"Mandate{suffix}"])
            code = _number(row["KandidatHwNw"]) or 0
            if mandates != 1 or not code & bit:
                continue
            text = _text(row[f"GeBezKurz{suffix}"])
            if not text:
                continue
            if _is_president(text, "" if not suffix else _text(row["GeBezKurz"]), main=not suffix):
                groups[(row["BfsNrGemeinde"], row["Sonntag"], suffix, row["GeLfNr"])].append(row)
    out: list[Round] = []
    for (bfs, day, suffix, _), rows in groups.items():
        first = rows[0]
        candidates: dict[str, Candidate] = {}
        circles: dict[str, list[dict[str, str]]] = defaultdict(list)
        for row in rows:
            circles[row["EinheitCode"]].append(row)
            last = _text(row["Nachname"])
            scattered = last.lower() == SCATTERED
            if last.lower() == "reserve":
                continue
            ident = SCATTERED if scattered else f"{row['Nachname']}|{row['Vorname']}|{row['Jahrgang']}"
            candidates.setdefault(
                ident,
                Candidate(
                    ident,
                    _text(row["Vorname"]),
                    last,
                    "" if scattered else _text(row["Partei"]),
                    "",
                    {"W": True, "M": False}.get(row["Geschlecht"]),
                    row[f"Bisher{suffix}"] == "1",
                    row[f"Gewaehlt{suffix}"] == "1",
                    scattered,
                ),
            )
        flat: list[Circle] = []
        for code, members in sorted(circles.items()):
            head = members[0]
            lines = (_number(head[f"StimmenLeer{suffix}"]) or 0) + (_number(head[f"StimmenUngueltig{suffix}"]) or 0)
            valid = _number(head["StmGueltig"])
            # The file gives each candidate's votes for the counting circle and for the electoral district, and a few
            # communes filled one of the two columns with something else. Take the column whose votes, with the
            # blank and invalid lines, add up to the valid ballots; the fuller column when neither does.
            options: list[dict[str, int | None]] = []
            for column in ("AuszKreis", "Wahlkreis"):
                votes: dict[str, int | None] = {}
                for row in members:
                    last = _text(row["Nachname"])
                    if last.lower() == "reserve":
                        continue
                    ident = (
                        SCATTERED
                        if last.lower() == SCATTERED
                        else f"{row['Nachname']}|{row['Vorname']}|{row['Jahrgang']}"
                    )
                    votes[ident] = _number(row[f"Stimmen{suffix}{column}"])
                options.append(votes)
            votes = next(
                (v for v in options if sum(x or 0 for x in v.values()) + lines == valid),
                max(options, key=lambda v: sum(x or 0 for x in v.values())),
            )
            cast = _number(head["StmAbgegeben"])
            invalid = (_number(head["StmEingelegtUngueltig"]) or 0) + (_number(head["StmUngueltig"]) or 0)
            flat.append(
                Circle(
                    code,
                    _text(head["Gemeinde"]),
                    _number(head["Stimmberechtigte"]),
                    cast,
                    invalid if cast is not None else None,
                    _number(head["StmLeer"]),
                    _number(head["StmGueltig"]),
                    votes,
                    lines,
                )
            )
        out.append(
            Round(
                f"{int(bfs):04d}",
                _text(first["Gemeinde"]),
                date.fromisoformat(day),
                _text(first[f"GeBezKurz{suffix}"]),
                _number(first[f"AbsolutesMehr{suffix}"]),
                list(candidates.values()),
                flat,
            )
        )
    return out


def rounds() -> dict[int, dict[str, list[Round]]]:
    """The presidential elections of each year by commune, in the order of their days. A commune with two
    presidential elections on one day is left out, because the file does not say which is which."""
    found: dict[int, dict[str, list[Round]]] = {}
    joined = _communes()
    called = dict(zip(joined["code"], joined["name"], strict=True))
    for year, built in ((2022, _rounds_2022()), (2026, _rounds_2026())):
        by: dict[str, list[Round]] = defaultdict(list)
        for item in sorted(built, key=lambda item: (item.bfs, item.day, item.description)):
            by[item.bfs].append(item)
        keep: dict[str, list[Round]] = {}
        for bfs, items in by.items():
            days = [item.day for item in items]
            if len(set(days)) != len(days) or len(items) > 2:
                continue
            keep[bfs] = [item._replace(name=called.get(bfs, item.name)) for item in items]
        found[year] = keep
    return found


def _communes() -> pl.DataFrame:
    areas = pl.read_parquet(REFERENCE / "area.parquet").filter(pl.col("scheme_code") == "ch.commune")
    codes = pl.read_parquet(REFERENCE / "area_code.parquet").filter(pl.col("valid_to").is_null())
    return areas.join(codes, left_on="id", right_on="area_id")


def areas() -> dict[str, str]:
    joined = _communes()
    return dict(zip(joined["code"], joined["id"], strict=True))


def polity(bfs: str) -> str:
    return key("polity", bfs)


def office(bfs: str) -> str:
    return key("office", bfs)


def party(label: str) -> str:
    return key("party", label)


def _independent(label: str) -> bool:
    return label.lower() in INDEPENDENT


def registry(found: dict[int, dict[str, list[Round]]]) -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "Zürich communes as polities with their presidencies, the two-ballot majority system, and the parties "
        "that put up candidates for communal president",
    )
    place = areas()
    p.external |= {CH, CH_ZH, *place.values()}
    p.add(
        "source",
        id=CH_SOURCE,
        kind="official_body",
        publisher="Amt für Statistik und Daten Kanton Zürich",
        title="Wahlresultate Gemeindebehördenwahlen im Kanton Zürich",
        url=PAGES[2026],
        license="Open use; must provide the source",
        license_url="https://opendata.swiss/en/terms-of-use#terms_by",
        attribution="Amt für Statistik und Daten Kanton Zürich",
    )
    for url in FILES.values():
        p.artifact(raw.cached(SOURCE, url), CH_SOURCE)
    p.add("scheme", code=SCHEME, polity_id=CH, kind="electoral_district", rank=3, noun="communal electorate")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=CH,
        valid_from=date(2000, 1, 1),
        valid_to=None,
        rules={"formula": "two_round", "majority": 0.5, "advance": 100},
        rules_version=1,
    )
    p.add("note", id=key("note", SYSTEM), entity_id=SYSTEM, kind="methodology", body_md=METHOD, lang="en")
    names: dict[str, str] = {}
    labels: dict[str, str] = {}
    for by in found.values():
        for bfs, items in by.items():
            for item in items:
                names[bfs] = item.name
                for candidate in item.candidates:
                    if candidate.party and not _independent(candidate.party) and not candidate.scattered:
                        labels[candidate.party] = max(labels.get(candidate.party, ""), candidate.party_name, key=len)
    for bfs, text in sorted(names.items()):
        if bfs not in place:
            continue
        id = polity(bfs)
        p.add("polity", id=id, kind="municipality", iso_code=None)
        p.add("polity_parent", polity_id=id, parent_id=CH_ZH)
        p.add("polity_territory", polity_id=id, area_id=place[bfs])
        p.name(id, text, "de", "official")
        p.add("external_id", entity_id=id, scheme="ch.bfs_gemeindenummer", value=bfs)
        post = office(bfs)
        p.add("office", id=post, polity_id=id, kind="mayor")
        p.name(post, f"President of {text}", "en", "official")
        p.name(post, f"Gemeindepräsidium {text}", "de", "native", False)
    for label, long in sorted(labels.items()):
        id = party(label)
        p.add("party", id=id, kind="party", polity_id=CH_ZH)
        p.add("external_id", entity_id=id, scheme="ch.zh_gemeinden.party", value=label)
        p.name(id, long or label, "de", "official")
        if long and long != label:
            p.name(id, label, "de", "abbreviation", False)
    return p


def election(year: int, by: dict[str, list[Round]], skipped: list[str]) -> Package:
    p = Package(
        f"{SOURCE}/zh-{year}",
        SOURCE,
        f"The {year} elections of the communal presidents in the canton of Zürich, first and second ballots, by "
        "commune and counting circle",
        depends=[f"{SOURCE}/registry"],
    )
    place = areas()
    p.external |= {CH, CH_ZH, *place.values()}
    p.uses(raw.cached(SOURCE, FILES[year]))
    pools: dict[tuple[date, int], str] = {}
    taken: set[tuple[str, str]] = set()
    for bfs, items in sorted(by.items()):
        if bfs not in place:
            skipped.append(f"{year} {bfs}")
            continue
        previous: str | None = None
        runner: dict[str, str] = {}
        for number, item in enumerate(items, start=1):
            pool = (item.day, number)
            path = f"ch/{item.day}-zh-communal-presidents" + ("" if number == 1 else "-runoff")
            if pool not in pools:
                id = pools[pool] = key("election", year, item.day, number)
                p.add(
                    "election",
                    id=id,
                    polity_id=CH_ZH,
                    kind="local" if number == 1 else "runoff",
                    date_from=item.day,
                    date_to=item.day,
                    status="held",
                )
                p.add("slug", path=path, entity_id=id, is_current=True)
                suffix = "" if number == 1 else " runoff"
                p.name(
                    id,
                    f"{item.day:%B} {item.day.day}, {item.day.year} Zürich communal presidential elections{suffix}",
                    "en",
                    "official",
                )
                p.name(
                    id,
                    f"Gemeindepräsidiumswahlen im Kanton Zürich am {item.day:%d.%m.%Y}"
                    + ("" if number == 1 else ", zweiter Wahlgang"),
                    "de",
                    "native",
                    False,
                )
            election_id = pools[pool]
            contest = key("contest", bfs, item.day)
            result_set = key("result_set", bfs, item.day)
            p.add(
                "contest",
                id=contest,
                election_id=election_id,
                kind="office",
                office_id=office(bfs),
                district_area_id=place[bfs],
                magnitude=1,
                round_number=number,
                previous_round_id=previous,
                electoral_system_id=SYSTEM,
                default_result_kind="counted",
            )
            previous = contest
            p.name(
                contest,
                f"{item.day.year} election of the president of {item.name}"
                + ("" if number == 1 else ", second ballot"),
                "en",
                "official",
            )
            tail = slug(item.name)
            if (path, tail) in taken:
                tail = f"{tail}-{bfs}"
            taken.add((path, tail))
            p.add("slug", path=f"{path}/{tail}", entity_id=contest, is_current=True)
            p.add(
                "result_set",
                id=result_set,
                election_id=election_id,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=CH_SOURCE,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            majority = f" Absolute majority: {item.majority} votes." if item.majority and item.majority > 0 else ""
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=METHOD + majority,
                lang="en",
            )
            options: dict[str, str] = {}
            won = any(candidate.elected for candidate in item.candidates)
            later = items[number].candidates if number < len(items) else []
            again = {candidate.key for candidate in later}
            for position, candidate in enumerate(item.candidates, start=1):
                option = options[candidate.key] = key("option", bfs, item.day, candidate.key)
                printed = " ".join(part for part in (candidate.first, candidate.last) if part)
                if candidate.scattered:
                    p.add(
                        "contest_option",
                        id=option,
                        contest_id=contest,
                        kind="others",
                        ballot_position=position,
                        ballot_label=printed,
                    )
                    continue
                label = candidate.party
                independent = _independent(label)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=None if independent else party(label),
                    ballot_position=position,
                    ballot_label=printed,
                )
                if candidate.key not in runner:
                    person = runner[candidate.key] = key("person", year, bfs, candidate.key)
                    p.add("person", id=person, gender={True: "female", False: "male"}.get(candidate.female))
                    p.name(person, printed, "de", "official")
                p.add(
                    "candidacy",
                    id=key("candidacy", bfs, item.day, candidate.key),
                    person_id=runner[candidate.key],
                    option_id=option,
                    role="head",
                    ballot_name=printed,
                    is_independent=independent,
                    is_incumbent=candidate.incumbent,
                )
                if candidate.elected:
                    kind = "elected"
                elif won:
                    kind = "lost"
                else:
                    kind = "advanced" if candidate.key in again else "eliminated"
                if won or number < len(items):
                    p.add("outcome", result_set_id=result_set, option_id=option, kind=kind)
            _units(p, bfs, item, number, options, result_set, place[bfs])
    p.accept(
        "accounting.votes_valid",
        "The commune's valid ballots include the ballots that carry only blank or invalid lines, and the source gives "
        "no option for those lines, so the candidates' votes with the scattered votes fall short of the valid ballots "
        "by the blank and invalid lines.",
    )
    return p


def _units(p: Package, bfs: str, item: Round, number: int, options: dict[str, str], result_set: str, area: str) -> None:
    many = len(item.circles) > 1
    top: str | None = None
    if many:
        top = key("unit", bfs, item.day)
        p.add(
            "reporting_unit",
            id=top,
            polity_id=CH_ZH,
            scheme_code=SCHEME,
            code=f"{item.day}:{bfs}",
            area_relation="equals",
            area_id=area,
            kind="aggregate",
        )
    totals: dict[tuple[str | None, int], int] = defaultdict(int)
    for circle in item.circles:
        unit = key("unit", bfs, item.day, circle.code) if many else key("unit", bfs, item.day)
        if many:
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CH_ZH,
                scheme_code=SCHEME,
                code=f"{item.day}:{bfs}:{circle.code}",
                parent_id=top,
                area_relation="within",
                area_id=area,
                kind="standard",
            )
        else:
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=CH_ZH,
                scheme_code=SCHEME,
                code=f"{item.day}:{bfs}",
                area_relation="equals",
                area_id=area,
                kind="standard",
            )
        figures: list[tuple[str | None, int, int | None]] = [
            (None, 2, circle.registered),
            (None, 3, circle.cast),
            (None, 5, circle.invalid),
            (None, 6, circle.blank),
            (None, 4, circle.valid),
        ]
        figures += [(options[ident], 1, value) for ident, value in circle.votes.items() if ident in options]
        for option, measure, value in figures:
            if value is None:
                continue
            totals[(option, measure)] += value
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )
    if top:
        for (option, measure), value in sorted(totals.items(), key=lambda pair: (pair[0][1], str(pair[0][0]))):
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=top,
                option_id=option,
                channel=0,
                measure=measure,
                count_round=0,
                flags=0,
                value=value,
            )


def reconcile(found: dict[int, dict[str, list[Round]]]) -> dict[int, tuple[int, list[str]]]:
    """Each counting circle's valid ballots against the candidates' votes, the scattered votes, and the blank and
    invalid lines the commune counted on them. The count of circles and the communes that differ."""
    out: dict[int, tuple[int, list[str]]] = {}
    for year, by in found.items():
        bad: list[str] = []
        total = 0
        for items in by.values():
            for item in items:
                for circle in item.circles:
                    total += 1
                    got = sum(value or 0 for value in circle.votes.values()) + (circle.lines or 0)
                    if circle.valid is not None and got != circle.valid:
                        bad.append(f"{item.bfs} {item.day} {circle.code}: {got} against {circle.valid}")
        out[year] = (total, bad)
    return out


def packages() -> list[str]:
    return [f"{SOURCE}/registry", *(f"{SOURCE}/zh-{year}" for year in FILES)]


def parse() -> None:
    found = rounds()
    for year, (total, bad) in reconcile(found).items():
        print(f"{year}: {total - len(bad)} of {total} counting circles reconcile to the valid ballots")
        for line in bad:
            print("  ", line)
    registry(found).write()
    skipped: list[str] = []
    for year in FILES:
        election(year, found[year], skipped).write()
    if skipped:
        print("communes without an area:", ", ".join(skipped))


def emit() -> None:
    package.run("load", packages())
