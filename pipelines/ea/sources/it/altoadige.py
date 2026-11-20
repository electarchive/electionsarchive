"""The Autonomous Province of Bolzano's provincial (Landtag) elections of 2008, 2013, 2018, and 2023, by municipality.

The Province publishes the results of each election on its open data portal (CC BY), as CSV files of the list votes
of each municipality, with its blank and null ballots, and of its turnout, with its registered voters. Since 1973 the
two provincial councils of Trentino-Alto Adige, elected together, form the regional council, so each province's
election is held by its own polity (IT-BZ here, IT-TN in `it.trento`) under the region's polity IT-32.

South Tyrol's 35 seats are allocated by proportional representation across the province, and the council elects the
Landeshauptmann, so there is no contest for President. Production already holds the region and the municipalities,
so the packages reference them; the province's own area is drawn in `it.altoadige/geo` from ISTAT's boundaries as
geoBoundaries republishes them.
"""

import csv
import html
import io
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from ea import package, raw
from ea.package import Package
from ea.sources.it import taa
from ea.sources.it.interno import BLANK, INVALID, REGISTERED, VOTERS, VOTES

SOURCE = "it.altoadige"
PROVINCE = taa.BOLZANO
POLITY = PROVINCE.polity
DATASETS = "https://data.civis.bz.it/dataset"
HUB = "https://datahub.civis.bz.it/autonomous-province-of-bolzano/elezioni-provinciali-2023"
STATIC = "https://static.provinz.bz.it/vote"
# The open data files give the postal votes of voters living abroad under this code, as if a municipality.
POSTAL = "021120"


@dataclass(frozen=True)
class Year:
    year: int
    day: date
    dataset: str
    lists: str
    turnout: str
    legislature: str
    summary: str | None = None
    column: int = 0


YEARS = {
    2008: Year(
        2008,
        date(2008, 10, 26),
        f"{DATASETS}/elezioni-provinciali-2008",
        f"{DATASETS}/64632448-7774-407b-91fe-51a16e1f793b/resource/dcd10436-ffa6-4d0d-9ce3-bfa23642b671/download/"
        "explistvotes2008.csv",
        f"{DATASETS}/64632448-7774-407b-91fe-51a16e1f793b/resource/a94072c3-6e17-4eac-abdb-2db6854e9bf6/download/"
        "expturnout2008.csv",
        "XIV",
        f"{STATIC}/landtag2013/results/dlistvt_li_vg.htm",
        1,
    ),
    2013: Year(
        2013,
        date(2013, 10, 27),
        f"{DATASETS}/elezioni-provinciali-2013",
        f"{DATASETS}/404ca2a9-e471-48a6-b24a-fcef4ecdb60b/resource/bdbb212a-511b-4ceb-8aeb-fb360595aeff/download/"
        "explistvotes2013.csv",
        f"{DATASETS}/404ca2a9-e471-48a6-b24a-fcef4ecdb60b/resource/113894d2-1311-444a-a8fa-daedf7d3967b/download/"
        "expturnout2013.csv",
        "XV",
        f"{STATIC}/landtag2013/results/dlistvt_li_vg.htm",
        0,
    ),
    2018: Year(
        2018,
        date(2018, 10, 21),
        f"{DATASETS}/elezioni-provinciali-2018",
        f"{DATASETS}/7432b1bc-68a5-4bfc-acbe-be21d95ce1e0/resource/53d4f927-fe11-4a0b-9dac-232d59047503/download/"
        "votilistalistenstimmen.csv",
        f"{DATASETS}/7432b1bc-68a5-4bfc-acbe-be21d95ce1e0/resource/09b69fc8-b525-4b6c-8430-45f73a15ed1b/download/"
        "affluenzawahlbeteiligung.csv",
        "XVI",
        f"{STATIC}/landtag2018/results/dlistvt_li_vg.htm",
        0,
    ),
    2023: Year(
        2023,
        date(2023, 10, 22),
        f"{DATASETS}/elezioni-provinciali-2023",
        f"{HUB}/VOTILISTA_LISTENSTIMMEN.CSV",
        f"{HUB}/AFFLUENZA_WAHLBETEILIGUNG.CSV",
        "XVII",
    ),
}


def fetch() -> None:
    for year in YEARS.values():
        raw.fetch(SOURCE, year.lists, suffix=".csv")
        raw.fetch(SOURCE, year.turnout, suffix=".csv")
        if year.summary is not None:
            raw.fetch(SOURCE, year.summary, suffix=".html")
    taa.fetch()


def table(file_url: str) -> list[dict[str, str]]:
    """A CSV file's rows. The files differ in encoding (UTF-8, some with a byte-order mark, or Latin-1) and in
    delimiter (semicolon or tab)."""
    content = raw.cached(SOURCE, file_url).path.read_bytes()
    try:
        text = content.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = content.decode("cp1252")
    header = text.split("\n", 1)[0]
    delimiter = "\t" if header.count("\t") > header.count(";") else ";"
    rows = list(csv.DictReader(io.StringIO(text), delimiter=delimiter))
    return [{key.strip().lower(): (value or "").strip() for key, value in row.items()} for row in rows]


def number(text: str) -> int:
    return int(re.sub(r"[.\s]", "", text) or "0")


def norm(label: str) -> str:
    label = unicodedata.normalize("NFKD", label.replace("’", "'").replace("`", "'"))
    label = "".join(c for c in label if not unicodedata.combining(c)).upper()
    return re.sub(r"\s+", " ", label).strip()


@dataclass
class Place:
    """One municipality's figures, or the postal votes'."""

    votes: dict[str, int] = field(default_factory=dict)
    blank: int = 0
    invalid: int = 0
    voters: int = 0
    registered: int = 0


@dataclass
class Summary:
    """The province's totals as its results page prints them."""

    votes: list[tuple[str, int, int]]
    blank: int
    invalid: int
    voters: int


def places(year: Year) -> dict[str, Place]:
    out: dict[str, Place] = {}
    old = year.year < 2018
    for row in table(year.lists):
        code = row["comm_istat" if old else "muni_num"]
        place = out.setdefault(code, Place())
        label = row["list_name"].strip()
        votes = number(row["listvotes" if old else "list_votes"])
        if label in place.votes:
            raise ValueError(f"{year.year} {code}: {label} appears twice")
        place.votes[label] = votes
        blank = number(row["blanks" if old else "muni_blank"])
        invalid = number(row["nulls" if old else "muni_nulls"])
        if place.voters and (place.blank, place.invalid) != (blank, invalid):
            raise ValueError(f"{year.year} {code}: the rows disagree on blank and null ballots")
        place.blank, place.invalid = blank, invalid
        if old:
            place.voters = number(row["ballots"])
        else:
            place.voters = -1
    for row in table(year.turnout):
        code = row["comm_istat" if old else "muni_num"]
        if code not in out:
            raise ValueError(f"{year.year} {code}: turnout for a place without votes")
        if old:
            out[code].registered = number(row["vt_hasright"])
            if number(row["vt_voters22"]) != out[code].voters:
                raise ValueError(f"{year.year} {code}: the two files disagree on voters")
        elif row["muni_hh"] == "21":
            out[code].registered = number(row["muni_right_t"])
            out[code].voters = number(row["muni_voters_t"])
    return out


def summary(year: Year) -> Summary | None:
    """The list votes, seats, and ballots of the province, from the results page; a page shows two elections, the
    current one first and the one before second, and `year.column` picks which."""
    if year.summary is None:
        return None
    content = raw.cached(SOURCE, year.summary).path.read_text(encoding="utf-8", errors="replace")
    plain = re.sub(r"(?is)<script.*?</script>|<style.*?</style>", "", content)
    plain = re.sub(r"(?i)</t[dh]>", "|", plain)
    plain = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", plain)))
    blocks = plain.split("Lista| Voti| % | Seggi|")[1:]
    if len(blocks) != 2:
        raise ValueError(f"{year.year}: expected two tables of lists")
    block = blocks[year.column].split("Dati ")[0]
    votes = [
        (label.replace(" Preferenze", "").strip(), number(count), int(seats))
        for label, count, seats in re.findall(r"\s*([^|]+?)\s*\|\s*([\d.]+)\|\s*[\d,]+%\|\s*(\d+)\|", block)
    ]
    tail = plain[plain.index("Schede valide") :]

    def figure(label: str) -> int:
        match = re.search(re.escape(label) + r"[^|]*\|" + r"\s*([\d.]+)\s*\|\s*[\d,]+%\s*\|\s*([\d.]+)", tail)
        if match is None:
            raise ValueError(f"{year.year}: the page has no {label}")
        return number(match[1 + year.column])

    return Summary(votes, figure("Schede bianche"), figure("Schede nulle"), figure("Totale schede scrutinate"))


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in YEARS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------- building the packages


def ids() -> Package:
    return Package("", SOURCE, "")


def source_id(kind: str) -> str:
    return ids().id("source", kind)


def postal_unit() -> str:
    return ids().id("unit", POSTAL)


def matched(year: Year, totals: dict[str, int], page: Summary) -> dict[str, tuple[int, int]]:
    """The page's total and seats for each list of the files, matched by total, since the page names the lists
    differently (in 2008, by short names)."""
    by_total: dict[int, list[str]] = {}
    for label, votes in totals.items():
        by_total.setdefault(votes, []).append(label)
    out: dict[str, tuple[int, int]] = {}
    for label, votes, seats in page.votes:
        found = by_total.get(votes, [])
        if len(found) != 1:
            raise ValueError(f"{year.year}: {label} ({votes}) matches {len(found)} lists of the files")
        out[found[0]] = (votes, seats)
    if len(out) != len(totals):
        raise ValueError(f"{year.year}: the page and the files list different lists")
    return out


def geo() -> Package:
    p = Package(f"{SOURCE}/geo", SOURCE, "The area of the Autonomous Province of Bolzano, with its outline")
    taa.geo(p, PROVINCE)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Autonomous Province of Bolzano's polity, its Landtag, the unit of its postal votes from abroad, and the "
        "lists that stood in its provincial elections of 2008 to 2023",
        depends=[f"{SOURCE}/geo"],
    )
    p.add(
        "source",
        id=source_id("opendata"),
        kind="official_body",
        publisher="Provincia autonoma di Bolzano - Alto Adige",
        title="Open Data Alto Adige: Elezioni provinciali (2008, 2013, 2018, 2023)",
        url=f"{DATASETS}?q=elezioni-provinciali",
        license="CC BY 4.0",
        license_url="https://creativecommons.org/licenses/by/4.0/",
        attribution="Provincia autonoma di Bolzano - Alto Adige, Open Data Alto Adige",
    )
    p.add(
        "source",
        id=source_id("results"),
        kind="official_body",
        publisher="Provincia autonoma di Bolzano - Alto Adige",
        title="Elezioni provinciali: risultati (2013, 2018)",
        url=f"{STATIC}/landtag2018/results/home_li_vg.htm",
        license=taa.LICENSE,
        attribution="Provincia autonoma di Bolzano - Alto Adige, Centrale elettorale",
    )
    taa.polity(p, PROVINCE, p.id("body", "council"), 35)
    p.external |= {"it.estero"}
    p.add(
        "reporting_unit",
        id=postal_unit(),
        polity_id=POLITY,
        scheme_code="it.estero",
        code=POSTAL,
        area_relation="unlocated",
        kind="postal_district",
    )
    p.name(postal_unit(), "Voto per corrispondenza (elettori residenti all'estero)", "it", "official")
    p.name(postal_unit(), "Briefwahl", "de", "native", False)
    p.name(postal_unit(), "Postal votes from abroad", "en", "english", False)
    labels: dict[str, str] = {}
    for year in YEARS.values():
        for place in places(year).values():
            for label in place.votes:
                labels.setdefault(norm(label), label)
    for key, label in sorted(labels.items()):
        party = p.id("party", key)
        p.add("party", id=party, kind="list_vehicle", polity_id=taa.ITALY)
        p.name(party, label, "it" if not re.search(r"[ÄÖÜäöüß]|SÜD|VOLKSPARTEI|FREIHEIT", label) else "de", "official")
    return p


def election(year_number: int) -> Package:
    year = YEARS[year_number]
    data = places(year)
    page = summary(year)
    p = Package(
        f"{SOURCE}/{year.year}",
        SOURCE,
        f"The {year.year} South Tyrolean provincial election: votes for the Landtag's lists by municipality",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.artifact(raw.cached(SOURCE, year.lists), source_id("opendata"))
    p.artifact(raw.cached(SOURCE, year.turnout), source_id("opendata"))
    if year.summary is not None:
        p.artifact(raw.cached(SOURCE, year.summary), source_id("results"))
    area = taa.area_of(PROVINCE)
    election_id = p.id("election", year.year)
    p.add(
        "election",
        id=election_id,
        polity_id=POLITY,
        kind="general",
        date_from=year.day,
        date_to=year.day,
        status="held",
    )
    p.name(election_id, f"{year.year} South Tyrolean provincial election", "en", "official")
    p.name(election_id, f"Elezioni provinciali in Alto Adige del {year.year}", "it", "native", False)
    p.name(election_id, f"Landtagswahl in Südtirol {year.year}", "de", "native", False)
    path = f"{PROVINCE.path}/{year.day}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    note = (
        f"Votes are as the Autonomous Province of Bolzano's open data files for the {year.year} election give them, "
        "by municipality, with the postal votes of voters living abroad as a unit of their own. Null ballots include "
        "ballots with only null votes and contested votes not assigned."
    )
    if page is not None:
        note += " The province's totals and seats are those of the Province's results page."
    p.add("note", id=p.id("note", year.year), entity_id=election_id, kind="methodology", body_md=note, lang="en")
    contest = p.id("contest", year.year, "council")
    p.add(
        "contest",
        id=contest,
        election_id=election_id,
        kind="list_allocation",
        body_id=p.id("body", "council"),
        district_area_id=area,
        magnitude=35,
        default_result_kind="counted",
    )
    p.name(contest, f"{year.year} South Tyrolean provincial election, Landtag lists", "en", "official")
    p.add("slug", path=f"{path}/consiglio", entity_id=contest, is_current=True)
    result_set = p.id("result_set", year.year, "council")
    p.add(
        "result_set",
        id=result_set,
        election_id=election_id,
        contest_id=contest,
        stage="official",
        kind="counted",
        source_id=source_id("opendata"),
        status="active",
        is_default=True,
        completeness=1.0,
    )
    totals: dict[str, int] = {}
    for place in data.values():
        for label, votes in place.votes.items():
            totals[label] = totals.get(label, 0) + votes
    options: dict[str, str] = {}
    for position, label in enumerate(sorted(totals, key=lambda name: -totals[name]), 1):
        options[label] = p.id("option", year.year, "list", norm(label))
        p.add(
            "contest_option",
            id=options[label],
            contest_id=contest,
            kind="list",
            party_id=p.id("party", norm(label)),
            ballot_position=position,
            ballot_label=label.strip(),
        )

    def put(unit: str, option: str | None, measure: int, value: int) -> None:
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

    known = taa.units()
    for code, place in sorted(data.items()):
        unit = postal_unit() if code == POSTAL else taa.unit(code, year.day, known)
        if code != POSTAL:
            p.external.add(unit)
        if sum(place.votes.values()) + place.blank + place.invalid != place.voters:
            raise ValueError(f"{year.year} {code}: votes and ballots do not add up to voters")
        for label, votes in place.votes.items():
            put(unit, options[label], VOTES, votes)
        put(unit, None, BLANK, place.blank)
        put(unit, None, INVALID, place.invalid)
        put(unit, None, VOTERS, place.voters)
        put(unit, None, REGISTERED, place.registered)
    if page is not None:
        for label, (votes, seats) in matched(year, totals, page).items():
            p.add("declared", result_set_id=result_set, option_id=options[label], measure=VOTES, value=votes)
            if seats:
                p.add("outcome", result_set_id=result_set, option_id=options[label], kind="won", seats=seats)
        for measure, value in ((BLANK, page.blank), (INVALID, page.invalid), (VOTERS, page.voters)):
            p.add("declared", result_set_id=result_set, option_id=None, measure=measure, value=value)
    return p
