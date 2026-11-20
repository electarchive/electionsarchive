import re
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import date
from functools import cache
from uuid import uuid5

import polars as pl

from ea import package, raw
from ea.package import Package
from ea.store import DATA

SOURCE = "ch.zh_gemeinde"
OGD = "https://daten.statistik.zh.ch/ogd/daten/ressourcen/{}.csv"
FILE_2026 = "KTZH_00003165_00006820"
FILE_2022 = "KTZH_00001281_00002419"
YEARS = {2022: FILE_2022, 2026: FILE_2026}
REGISTRY = f"{SOURCE}/registry"
REFERENCE = DATA / "reference" / "ch"
# Bodies other than the communal executive, which the source files list beside it.
OTHER_BODY = re.compile(
    r"schul|rpk|rechnungs|sozial|\bbau(?:kommission|ausschuss|beh)|kirch|vormund|f[üu]rsorge|kommission|pflege"
    r"|wahlb|controll|werk|gesundheit|notar|friedens|betreib|versamml|\bsek|prim|\bekk\b|gpk|gesch[äa]ftspr"
    r"|kesb|b[üu]rgerrecht|soko|spf|pso|psp|bapf|\bbrk\b|geko|gesellschaft|sobe",
    re.IGNORECASE,
)
EXECUTIVE = re.compile(
    r"gemeinderat|stadtrat|gemeindevorstand|gemeindepr|stadtpr|\bgr\b|gemeinderats|präsi", re.IGNORECASE
)
PRESIDENCY = re.compile(r"pr[äa]sid|pr[äa]si\b|pr[äa]sum", re.IGNORECASE)


def url(year: int) -> str:
    return OGD.format(YEARS[year])


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, url(year), suffix=".csv")


def table(year: int) -> pl.DataFrame:
    return pl.read_csv(raw.cached(SOURCE, url(year)).path, infer_schema_length=0)


def office(title: str, mandates: int) -> str | None:
    """The executive office a contest fills, from its printed titles, or None for any other body."""
    if OTHER_BODY.search(title) or not EXECUTIVE.search(title):
        return None
    if mandates == 1 and PRESIDENCY.search(title) and not re.search(r"(ein|1)(es)?\s+mitglied", title, re.IGNORECASE):
        return "president"
    return "council"


@dataclass
class Candidate:
    ref: str
    family: str
    first: str | None
    party: str | None
    party_long: str | None
    born: int | None
    sex: str | None
    incumbent: bool | None
    votes: int | None
    elected: bool
    drawn: bool = False


@dataclass
class Contest:
    year: int
    bfs: str
    commune: str
    office: str
    day: date
    mandates: int
    title: str
    ident: str
    majority: int | None = None
    voters: int | None = None
    cast: int | None = None
    invalid_ballots: int | None = None
    invalid_counted: int = 0
    blank_ballots: int | None = None
    valid_ballots: int | None = None
    invalid_votes: int | None = None
    blank_votes: int | None = None
    individual_votes: int | None = None
    round: int = 1
    candidates: list[Candidate] = field(default_factory=list)


def num(value: str | None) -> int | None:
    if value in (None, "", "NA"):
        return None
    return int(float(value))


def contests_2026() -> tuple[list[Contest], list[str]]:
    data = table(2026).filter(pl.col("domainOfInfluence_domainOfInfluenceType") == "MU")
    skipped: list[str] = []
    found: dict[str, Contest] = {}
    circles: dict[str, dict[str, dict[str, str | None]]] = defaultdict(dict)
    totals: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    votes: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    people: dict[str, dict[str, Candidate]] = defaultdict(dict)
    for row in data.iter_rows(named=True):
        title = f"{row['electionDescriptionInfo-de_electionDescriptionShort']} {row['electionDescriptionInfo-de_electionDescription']}"
        mandates = num(row["election_numberOfMandates"]) or 0
        kind = office(title, mandates)
        if kind is None:
            continue
        if row["stilleWahl"] == "1" or row["pollingDay"] is None:
            skipped.append(f"{row['domainOfInfluence_domainOfInfluenceName']}: {title}")
            continue
        ident = row["electionIdentification"]
        if ident not in found:
            found[ident] = Contest(
                year=2026,
                bfs=f"{int(row['domainOfInfluence_domainOfInfluenceIdentification']):04d}",
                commune=row["domainOfInfluence_domainOfInfluenceName"],
                office=kind,
                day=date.fromisoformat(row["pollingDay"]),
                mandates=mandates,
                title=row["electionDescriptionInfo-de_electionDescriptionShort"],
                ident=ident,
                majority=num(row["absoluteMajority"]),
            )
        circle = row["countingCircle_countingCircleId"]
        if circle not in circles[ident]:
            circles[ident][circle] = row
            for name, column in (
                ("voters", "countOfVotersInformation_countOfVotersTotal"),
                ("cast", "resultData_countOfReceivedBallots"),
                ("invalid_ballots", "resultData_countOfInvalidBallots"),
                ("blank_ballots", "resultData_countOfBlankBallots"),
                ("valid_ballots", "resultData_countOfValidBallots"),
                ("invalid_votes", "countOfInvalidVotesTotal"),
                ("blank_votes", "countOfBlankVotesTotal"),
                ("individual_votes", "countOfIndividualVotesTotal"),
            ):
                totals[ident][name] += num(row[column]) or 0
        who = row["candidateIdentification"]
        votes[ident][who] += num(row["countOfVotesTotal"]) or 0
        if who not in people[ident]:
            people[ident][who] = Candidate(
                ref=who,
                family=row["candidate_politicalFamilyName"] or row["candidate_familyName"],
                first=row["candidate_politicalFirstName"] or row["candidate_firstName"],
                party=row["partyAffiliationInfo-de_partyAffiliationShort"],
                party_long=row["partyAffiliationInfo-de_partyAffiliationLong"],
                born=num(row["candidate_yearOfBirth"]),
                sex={"1": "male", "2": "female"}.get(row["candidate_sex"] or ""),
                incumbent={"1": True, "0": False}.get(row["candidate_incumbentYesNo"] or ""),
                votes=None,
                elected=row["elected"] == "1",
                drawn=row["electedCandidate_isElectedByDraw"] == "1",
            )
    for ident, contest in found.items():
        for name, value in totals[ident].items():
            setattr(contest, name, value)
        for who, person in people[ident].items():
            person.votes = votes[ident][who]
            contest.candidates.append(person)
    return list(found.values()), skipped


def contests_2022() -> tuple[list[Contest], list[str]]:
    """The 2022 file has one row per candidate and counting circle. A row group holds the council vote in the main
    columns and the presidency vote, on the same ballot, in the NW columns."""
    data = table(2022)
    skipped: list[str] = []
    groups: dict[tuple[str, str, str], list[dict[str, str | None]]] = defaultdict(list)
    for row in data.iter_rows(named=True):
        title = f"{row['GeBezKurz']} {row['GeBezOffiziell']}"
        if office(title, num(row["Mandate"]) or 0) is None:
            continue
        if row["StilleWahl"] == "1" or row["Sonntag"] in (None, "NA"):
            skipped.append(f"{row['Gemeinde']}: {row['GeBezKurz']}")
            continue
        groups[(row["BfsNrGemeinde"], row["Sonntag"], row["GeLfNr"])].append(row)
    found: list[Contest] = []
    for (bfs, day, ident), rows in groups.items():
        first = rows[0]
        main = office(f"{first['GeBezKurz']} {first['GeBezOffiziell']}", num(first["Mandate"]) or 0)
        parts = [("", main)]
        if main == "council":
            for suffix in ("NW", "NW2"):
                if first[f"GeBezKurz{suffix}"] == "NA":
                    continue
                other = office(
                    f"{first[f'GeBezKurz{suffix}']} {first[f'GeBezOffiziell{suffix}']}",
                    num(first[f"Mandate{suffix}"]) or 0,
                )
                if other is not None:
                    parts.append((suffix, other))
        for suffix, kind in parts:
            contest = Contest(
                year=2022,
                bfs=f"{int(bfs):04d}",
                commune=first["Gemeinde"] or "",
                office=kind or "council",
                day=date.fromisoformat(day),
                mandates=num(first[f"Mandate{suffix}"]) or 0,
                title=first[f"GeBezKurz{suffix}"] or "",
                ident=f"{ident}{suffix}",
                majority=num(first[f"AbsolutesMehr{suffix}"]),
            )
            circles: dict[str, dict[str, str | None]] = {}
            for row in rows:
                circles.setdefault(row["EinheitCode"] or "", row)
            for name, column in (
                ("voters", "Stimmberechtigte"),
                ("cast", "StmAbgegeben"),
                ("invalid_ballots", "StmEingelegtUngueltig"),
                ("invalid_counted", "StmUngueltig"),
                ("blank_ballots", "StmLeer"),
                ("valid_ballots", "StmGueltig"),
                ("invalid_votes", f"StimmenUngueltig{suffix}"),
                ("blank_votes", f"StimmenLeer{suffix}"),
            ):
                setattr(contest, name, sum(num(r[column]) or 0 for r in circles.values()))
            contest.invalid_ballots = (contest.invalid_ballots or 0) + contest.invalid_counted
            people: dict[str, Candidate] = {}
            votes: dict[str, int] = defaultdict(int)
            for row in rows:
                code = num(row["KandidatHwNw"])
                if code is not None and not code & {"": 1, "NW": 2, "NW2": 4}[suffix]:
                    continue
                who = row["KNR"] or ""
                votes[who] += num(row[f"Stimmen{suffix}AuszKreis"]) or 0
                if who not in people:
                    people[who] = Candidate(
                        ref=f"{ident}:{who}",
                        family=row["Nachname"] or "",
                        first=None if row["Vorname"] in (None, "NA") else row["Vorname"],
                        party=None if row["Partei"] in (None, "NA") else row["Partei"],
                        party_long=None,
                        born=num(row["Jahrgang"]),
                        sex={"M": "male", "W": "female", "F": "female"}.get(row["Geschlecht"] or ""),
                        incumbent={"1": True, "0": False}.get(row[f"Bisher{suffix}"] or ""),
                        votes=None,
                        elected=row[f"Gewaehlt{suffix}"] == "1",
                    )
            for who, person in people.items():
                person.votes = votes[who]
                if person.family == "Vereinzelte":
                    contest.individual_votes = (contest.individual_votes or 0) + votes[who]
                else:
                    contest.candidates.append(person)
            found.append(contest)
    return found, skipped


def contests() -> tuple[list[Contest], list[str]]:
    found: list[Contest] = []
    skipped: list[str] = []
    for part, left in (contests_2022(), contests_2026()):
        for contest in part:
            contest.candidates = [c for c in contest.candidates if c.family != "Reserve"]
            if contest.voters == 0:
                contest.voters = None
            if contest.mandates == 0 or sum(c.votes or 0 for c in contest.candidates) == 0:
                skipped.append(f"{contest.commune} {contest.day}: {contest.title} (no votes counted)")
            else:
                found.append(contest)
        skipped.extend(left)
    return found, skipped


PUBLISHER = "Amt für Statistik und Daten des Kantons Zürich"
INDEPENDENT = {"parteilos", "ohne", "ohne partei", "unabhängig", "unabhaengig", "-", "bisher", "", "na"}
NATIONAL = {
    "fdp": "FDP",
    "svp": "SVP",
    "sp": "SP",
    "glp": "GLP",
    "evp": "EVP",
    "edu": "EDU",
    "al": "AL",
    "pda": "PdA",
    "juso": "JUSO",
    "mitte": "Mitte",
    "die mitte": "Mitte",
    "grüne": "GRÜNE",
    "gp": "GRÜNE",
}
BALLOTS = ((2, "voters"), (3, "cast"), (5, "invalid_ballots"), (6, "blank_ballots"), (4, "valid_ballots"))
METHOD = (
    "Counted by the commune and published by the cantonal statistics office. Registered voters, ballots cast, and "
    "valid ballots are summed over the counting circles of the commune (Zurich and Winterthur count in several). "
    "Invalid ballots are the ballots put in that were invalid or found invalid in the count. Each valid ballot "
    "carries as many lines as the council has seats open (one for a presidency), so the votes for candidates, the "
    "scattered votes written on lines (Vereinzelte), the blank lines, and the invalid lines add up to the valid "
    "ballots times the seats open. Blank and invalid lines are not stored here. A candidate is elected in a round "
    "with an absolute majority of the counted votes; seats still open go to a second round, where the most votes win."
)
COUNCIL_SYSTEM = "Gemeinderat or Stadtrat members are elected by majority vote. In the first round a candidate needs an absolute majority, and in later rounds the candidates with the most votes win the seats still open; a tie is settled by lot."
PRESIDENT_SYSTEM = "The presidency of the council is elected by majority vote. In the first round a candidate needs an absolute majority; in a second round the candidate with the most votes wins, and a tie is settled by lot."


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


@cache
def places() -> tuple[str, dict[str, tuple[str, str, str]]]:
    """The id of Switzerland, and the commune unit, area, and name that production holds for each BFS number."""
    polity = pl.read_parquet(REFERENCE / "polity.parquet")
    country = polity.filter(pl.col("iso_code") == "CH")["id"][0]
    units = pl.read_parquet(REFERENCE / "reporting_unit.parquet").filter(
        (pl.col("scheme_code") == "ch.commune") & (pl.col("kind") == "standard")
    )
    names = pl.read_parquet(REFERENCE / "area.parquet").select(area_id="id", name="name")
    joined = units.join(names, on="area_id")
    return country, {r["code"]: (r["id"], r["area_id"], r["name"]) for r in joined.iter_rows(named=True)}


def slugify(text: str) -> str:
    text = text.replace("ä", "ae").replace("ö", "oe").replace("ü", "ue")
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def party_key(label: str | None) -> str | None:
    if label is None:
        return None
    text = re.sub(r"\s+", " ", label).strip()
    if text.casefold() in INDEPENDENT:
        return None
    return NATIONAL.get(text.casefold(), text)


def person_key(contest: Contest, who: Candidate) -> str:
    return key("person", contest.year, contest.bfs, who.family, who.first, who.born)


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{year}" for year in YEARS)]


def parse() -> None:
    found, _ = contests()
    rounds(found)
    registry(found).write()
    for year in YEARS:
        election(year, found).write()


def emit() -> None:
    package.run("load", packages())


def rounds(found: list[Contest]) -> None:
    """Number each commune's contests for one office in order of polling day."""
    days: dict[tuple[int, str, str], list[Contest]] = defaultdict(list)
    for contest in found:
        days[(contest.year, contest.bfs, contest.office)].append(contest)
    for series in days.values():
        for number, contest in enumerate(sorted(series, key=lambda c: c.day), 1):
            contest.round = number


def registry(found: list[Contest]) -> Package:
    country, towns = places()
    p = Package(
        REGISTRY,
        SOURCE,
        "Executive councils and presidencies of Zurich communes, the parties on their ballots, and the voting rules",
    )
    p.external.add(country)
    source = key("source", "zh-statistik")
    p.add(
        "source",
        id=source,
        kind="official_body",
        publisher=PUBLISHER,
        title="Wahlresultate Gemeindebehördenwahlen im Kanton Zürich",
        url="https://opendata.swiss/en/dataset/archiv-wahlresultate-gemeindebehordenwahlen-im-kanton-zurich",
        license="opendata.swiss terms of use: free reuse with the source named",
        license_url="https://opendata.swiss/terms-of-use#terms_by",
        attribution="Kanton Zürich, Amt für Statistik und Daten",
        notes=(
            "The communes count with the cantonal election software or report to the canton. Elections a commune "
            "ran on its own, and persons elected without a vote, may be missing."
        ),
    )
    for year in YEARS:
        p.artifact(raw.cached(SOURCE, url(year)), source)
    for kind, text in (("council", COUNCIL_SYSTEM), ("president", PRESIDENT_SYSTEM)):
        system = key("electoral_system", kind)
        rules = {"formula": "block" if kind == "council" else "fptp", "tieBreak": "lot"}
        p.add("electoral_system", id=system, polity_id=country, rules=rules, rules_version=1)
        p.add("note", id=key("note", system), entity_id=system, kind="methodology", body_md=text, lang="en")
    city: dict[str, bool] = defaultdict(bool)
    for contest in found:
        city[contest.bfs] = city[contest.bfs] or "stadt" in contest.title.casefold()
    for bfs in sorted({c.bfs for c in found}):
        _, _, name = towns[bfs]
        body = key("body", bfs)
        p.add("body", id=body, polity_id=country, kind="council")
        executive = "Stadtrat" if city[bfs] else "Gemeinderat"
        p.name(body, f"Communal executive of {name}", "en", "official")
        p.name(body, f"{executive} {name}", "de", "native", False)
        post = key("office", bfs)
        p.add("office", id=post, polity_id=country, body_id=body, kind="mayor")
        p.name(post, f"President of the communal executive of {name}", "en", "official")
        p.name(post, f"{'Stadtpräsidium' if city[bfs] else 'Gemeindepräsidium'} {name}", "de", "native", False)
    labels: dict[str, Counter[str]] = defaultdict(Counter)
    longs: dict[str, Counter[str]] = defaultdict(Counter)
    for contest in found:
        for who in contest.candidates:
            label = party_key(who.party)
            if label is not None:
                labels[label][who.party or label] += 1
                if who.party_long:
                    longs[label][who.party_long] += 1
    for label in sorted(labels):
        id = key("party", label)
        p.add("party", id=id, kind="party", polity_id=country)
        p.add("external_id", entity_id=id, scheme="ch.zh_gemeinde.party", value=label)
        full = longs[label].most_common(1)[0][0] if longs[label] else label
        p.name(id, full, "de", "official")
        if full != label:
            p.name(id, label, "de", "abbreviation", False)
    return p


def election(year: int, found: list[Contest]) -> Package:
    country, towns = places()
    mine = sorted((c for c in found if c.year == year), key=lambda c: (c.day, c.bfs, c.office))
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} renewal elections of the executive councils and presidencies of Zurich communes",
        depends=[REGISTRY],
    )
    p.external.add(country)
    p.uses(raw.cached(SOURCE, url(year)))
    source = key("source", "zh-statistik")
    previous: dict[tuple[str, str], str] = {}
    returning: dict[tuple[str, str, int], set[str]] = defaultdict(set)
    for c in mine:
        for earlier in range(1, c.round):
            returning[(c.bfs, c.office, earlier)] |= {person_key(c, w) for w in c.candidates}
    persons: set[str] = set()
    elections: dict[date, str] = {}
    for day in sorted({c.day for c in mine}):
        id = elections[day] = key("election", day)
        first = any(c.round == 1 for c in mine if c.day == day)
        p.add(
            "election",
            id=id,
            polity_id=country,
            kind="local" if first else "runoff",
            date_from=day,
            date_to=day,
            date_precision="day",
            status="held",
        )
        p.add("slug", path=f"ch/{day}-communal-zh", entity_id=id, is_current=True)
        suffix = "" if first else " (second rounds)"
        p.name(id, f"Zurich communal authority elections, {day:%-d %B %Y}{suffix}", "en", "official")
    for contest in mine:
        unit, area, name = towns[contest.bfs]
        p.external |= {unit, area}
        id = key("contest", contest.year, contest.bfs, contest.office, contest.round)
        if contest.office == "council":
            kind, body, post = "body_seats", key("body", contest.bfs), None
        else:
            kind, body, post = "office", None, key("office", contest.bfs)
        prior = previous.get((contest.bfs, contest.office))
        p.add(
            "contest",
            id=id,
            election_id=elections[contest.day],
            kind=kind,
            office_id=post,
            body_id=body,
            district_area_id=area,
            magnitude=contest.mandates,
            round_number=contest.round,
            previous_round_id=prior,
            electoral_system_id=key("electoral_system", contest.office),
            default_result_kind="counted",
        )
        previous[(contest.bfs, contest.office)] = id
        what = "council" if contest.office == "council" else "presidency"
        rnd = "" if contest.round == 1 else f", round {contest.round}"
        p.name(id, f"{name} communal {what} election {contest.day.year}{rnd}", "en", "official")
        p.name(id, contest.title, "de", "native", False)
        p.add(
            "slug",
            path=f"ch/{contest.day}-communal-zh/{slugify(name)}-{'council' if contest.office == 'council' else 'president'}",
            entity_id=id,
            is_current=True,
        )
        result_set = key("result_set", id)
        p.add(
            "result_set",
            id=result_set,
            election_id=elections[contest.day],
            contest_id=id,
            stage="official",
            kind="counted",
            source_id=source,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", result_set), entity_id=result_set, kind="methodology", body_md=METHOD, lang="en")
        for measure, field_name in BALLOTS:
            value = getattr(contest, field_name)
            if value is not None:
                p.add(
                    "result_value",
                    result_set_id=result_set,
                    unit_id=unit,
                    option_id=None,
                    channel=0,
                    measure=measure,
                    count_round=0,
                    flags=0,
                    value=value,
                )
        later = returning.get((contest.bfs, contest.office, contest.round), set())
        for position, who in enumerate(sorted(contest.candidates, key=lambda w: -(w.votes or 0)), 1):
            person_id = person_key(contest, who)
            if person_id not in persons:
                persons.add(person_id)
                born = date(who.born, 1, 1) if who.born else None
                p.add(
                    "person",
                    id=person_id,
                    birth_date=born,
                    birth_precision="year" if born else None,
                    gender=who.sex,
                )
                p.name(person_id, " ".join(x for x in (who.first, who.family) if x), "de", "official")
            option = key("option", id, person_id)
            label_party = party_key(who.party)
            party = key("party", label_party) if label_party else None
            ballot_name = " ".join(x for x in (who.family, who.first) if x)
            p.add(
                "contest_option",
                id=option,
                contest_id=id,
                kind="candidate",
                party_id=party,
                ballot_position=position,
                ballot_label=ballot_name,
            )
            p.add(
                "candidacy",
                id=key("candidacy", option),
                person_id=person_id,
                option_id=option,
                role="head",
                ballot_name=ballot_name,
                member_of_party_id=party,
                is_independent=label_party is None
                and (who.party or "").casefold() in INDEPENDENT - {"", "na", "bisher"},
                is_incumbent=who.incumbent,
            )
            if who.elected:
                outcome = "elected"
            elif person_id in later:
                outcome = "advanced"
            else:
                outcome = "lost"
            p.add("outcome", result_set_id=result_set, option_id=option, kind=outcome)
            p.add(
                "result_value",
                result_set_id=result_set,
                unit_id=unit,
                option_id=option,
                channel=0,
                measure=1,
                count_round=0,
                flags=0,
                value=who.votes,
            )
        if contest.individual_votes:
            option = key("option", id, "scattered")
            p.add(
                "contest_option",
                id=option,
                contest_id=id,
                kind="others",
                ballot_label="Vereinzelte",
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
                value=contest.individual_votes,
            )
    p.accept(
        "accounting.votes_valid",
        "A ballot is valid when any of its lines is valid, and the presidency is voted on the same ballot paper as "
        "the council, so a valid ballot can leave its presidency line blank or invalid. The source counts those "
        "lines apart from the votes for candidates, and they are not stored here, so the votes for candidates and "
        "scattered votes in a one-seat contest fall short of the valid ballots by exactly those lines.",
    )
    return p
