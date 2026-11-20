"""The Autonomous Province of Trento's provincial elections of 2003, 2008, 2013, 2018, and 2023, by municipality.

Since 1973 Trentino-Alto Adige's regional council has been formed by the councils of its two autonomous provinces,
which are elected together, so each province's election is held by its own polity (IT-TN here, IT-BZ in
`it.altoadige`) under the region's polity IT-32. Since 2003 Trento also elects its President directly, on a ballot
that carries the President's candidates and their linked lists.

The Province publishes one results site per election (elezioni-<year>.provincia.tn.it), with the votes for each
candidate for President, each list, the blank and null ballots, and the voters of every municipality, and the
registered voters of the whole province on its turnout page. The packages hold those figures at the municipality,
and the province's totals as declared; the registered voters, which no municipality carries, are named in each
election's note. Production already holds the region, and the municipalities the Interior
Ministry and the municipal pipeline wrote, so the packages reference them; the province's own area, which the
provincial pipeline never wrote for the two autonomous provinces, is drawn in `it.trento/geo` from ISTAT's
boundaries as geoBoundaries republishes them.
"""

import html
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date

from ea import package, raw
from ea.package import Package
from ea.sources.it import taa
from ea.sources.it.interno import BLANK, INVALID, VOTERS, VOTES

SOURCE = "it.trento"
PROVINCE = taa.TRENTO
POLITY = PROVINCE.polity


@dataclass(frozen=True)
class Year:
    year: int
    day: date
    base: str
    president: str
    lists: str
    comune_president: str
    comune_lists: str
    turnout: str
    seats: int
    legislature: str


def _site(year: int) -> str:
    return f"http://www.elezioni-{year}.provincia.tn.it" if year < 2023 else "https://www.elezioni-2023.provincia.tn.it"


YEARS = {
    2003: Year(
        2003,
        date(2003, 10, 26),
        _site(2003),
        "000001/CoalizioniProv/AggregazioniPadre/000001.html",
        "000001/ListeProv/AggregazioniPadre/000001.html",
        "000001/CoalizioniProv/Comuni/{}.html",
        "000001/ListeProv/Comuni/{}.html",
        "000001/AffluenzaProv/Aggregazioni/000001_0.html",
        35,
        "XIII",
    ),
    2008: Year(
        2008,
        date(2008, 11, 9),
        _site(2008),
        "000017_Prov/Coalizioni/000001.html",
        "000017_Prov/Liste/000001.html",
        "000017_Prov/Coalizioni/{}.html",
        "000017_Prov/Liste/{}.html",
        "000017_Prov/Affluenza/22_000001.html",
        35,
        "XIV",
    ),
    2013: Year(
        2013,
        date(2013, 10, 27),
        _site(2013),
        "Risultati/Coalizioni/000538.html",
        "Risultati/Liste/000538.html",
        "Risultati/Coalizioni/{}.html",
        "Risultati/Liste/{}.html",
        "Risultati/Affluenza/000538.html",
        35,
        "XV",
    ),
    2018: Year(
        2018,
        date(2018, 10, 21),
        _site(2018),
        "Risultati/Coalizioni/000538.html",
        "Risultati/Liste/000538.html",
        "Risultati/Coalizioni/{}.html",
        "Risultati/Liste/{}.html",
        "Risultati/Affluenza/000538.html",
        35,
        "XVI",
    ),
    2023: Year(
        2023,
        date(2023, 10, 22),
        _site(2023),
        "Risultati/Coalizioni/000538.html",
        "Risultati/Liste/000538.html",
        "Risultati/Coalizioni/{}.html",
        "Risultati/Liste/{}.html",
        "Risultati/Affluenza/000538.html",
        35,
        "XVII",
    ),
}


def url(year: Year, path: str) -> str:
    return f"{year.base}/{path}"


def read(page_url: str) -> str:
    content = raw.cached(SOURCE, page_url).path.read_bytes()
    try:
        return content.decode("utf-8")
    except UnicodeDecodeError:
        return content.decode("cp1252", errors="replace")


def comune_codes(year: Year) -> list[str]:
    """The municipalities' codes (province_comune, as the site names its pages), from the summary page's menu."""
    page = read(url(year, year.president))
    if year.year == 2003:
        form = read(url(year, "form_prov_presidenti.html"))
        found = [f"022_{int(value):03d}" for value in re.findall(r"<option value=(\d+)>", form)]
        # The menu also lists the communities (comprensori) after the municipalities, numbered from 1 again.
        out: list[str] = []
        for code in found:
            if code in out:
                break
            out.append(code)
        return out
    if year.year == 2008:
        return sorted(set(re.findall(r'href="(022_\d{3})\.html"', page)))
    # The menu of links in 2023 still lists municipalities merged away in 2020; the drop-down list is current.
    return sorted(set(re.findall(r'value=\\?"(022_\d{3})', page)))


def summary_pages(year: Year) -> list[str]:
    """The province's pages, and in 2003 the menu that lists the municipalities."""
    paths = [year.president, year.lists, year.turnout]
    if year.year == 2003:
        paths.append("form_prov_presidenti.html")
    return paths


def fetch() -> None:
    taa.fetch()
    for year in YEARS.values():
        for path in summary_pages(year):
            raw.fetch(SOURCE, url(year, path), suffix=".html")
        pages = [
            url(year, pattern.format(code))
            for code in comune_codes(year)
            for pattern in (year.comune_president, year.comune_lists)
        ]
        got = raw.fetch_many(SOURCE, pages, suffix=".html", workers=6)
        missing = [page for page in pages if page not in got]
        if missing:
            raise ValueError(f"{year.year}: {len(missing)} pages could not be fetched, such as {missing[0]}")


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{year}" for year in YEARS)]


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())


# ---------------------------------------------------------------- reading the pages


def text(page: str) -> str:
    plain = re.sub(r"(?is)<script.*?</script>|<style.*?</style>|<select.*?</select>|<head.*?</head>", "", page)
    plain = re.sub(r"(?i)</t[dh]>", "|", plain)
    plain = html.unescape(re.sub(r"<[^>]+>", " ", plain))
    return re.sub(r"\s+", " ", plain)


def number(value: str) -> int:
    return int(value.replace(".", ""))


def norm(label: str) -> str:
    label = unicodedata.normalize("NFKD", label.replace("’", "'").replace("`", "'"))
    label = "".join(c for c in label if not unicodedata.combining(c)).upper()
    return re.sub(r"\s+", " ", label).strip()


@dataclass
class Count:
    """One page's figures: votes by candidate or list, in the order printed, and the ballots."""

    votes: dict[str, int] = field(default_factory=dict)
    blank: int = 0
    invalid: int = 0
    voters: int = 0
    valid: int = 0


def after(plain: str, marker: str) -> str:
    start = plain.find(marker)
    if start < 0:
        raise ValueError(f"the page has no {marker!r}")
    return plain[start + len(marker) :]


def closing(plain: str) -> int:
    """Where the page's closing figures (blank ballots first) begin."""
    match = re.search(r"Schede bianche", plain, re.IGNORECASE)
    if match is None:
        raise ValueError("the page has no blank ballots")
    return match.start()


def ballots(count: Count, plain: str) -> None:
    """Blank and null ballots and voters, from the labeled figures that close a page."""
    tail = plain[closing(plain) :]
    figures = {
        label.strip().lower(): number(value) for label, value in re.findall(r"([A-Za-z][A-Za-z ]+?):\s*([\d.]+)", tail)
    }
    null_labels = (
        "schede nulle",
        "schede nulle o contenenti solo voti nulli",
        "voti nulli",
        "schede contenenti solo voti nulli",
        "voti contestati",
        "voti contestati e non attribuiti",
    )
    count.blank = figures.get("schede bianche", 0)
    count.invalid = sum(figures.get(label, 0) for label in null_labels)
    total = figures.get("totale votanti", figures.get("totale voti"))
    if total is None:
        raise ValueError("the page has no total of voters")
    count.voters = total


def president(page: str) -> Count:
    plain = text(page)
    valid = re.search(r"Totale voti validi:\s*([\d.]+)", plain)
    marker = "CANDIDATO PRESIDENTE E VOTI/LISTE COLLEGATE"
    if marker not in plain:
        marker = "VOTI / LISTE APPOGGIO" if "VOTI / LISTE APPOGGIO" in plain else "voti/liste collegate"
    body = after(plain, marker)
    body = body[: closing(body)]
    count = Count(valid=number(valid[1]) if valid else 0)
    pattern = r"\s*([^\d%|/]+?)\s*(?:/|\|\s*N°:)?\s*([\d.]+)(?: voti DI CUI SOLO AL PRESIDENTE [\d.]+)?\s+[\d,]+%"
    for match in re.finditer(pattern, body.replace("| |", " ").replace("|", " | ")):
        name = match[1].strip(" |")
        if name in count.votes:
            raise ValueError(f"{name} appears twice")
        count.votes[name] = number(match[2])
    ballots(count, plain)
    return count


def lists(page: str) -> Count:
    plain = text(page)
    valid = re.search(r"Totale voti validi:\s*([\d.]+)", plain)
    body = after(plain, "RISULTATI LISTE")
    count = Count(valid=number(valid[1]) if valid else 0)
    if 'class="nome-partito"' in page:
        found = [
            (html.unescape(name), votes)
            for name, votes in re.findall(r'class="nome-partito">([^<]+)</span>.*?voti:\s*([\d.]+)', page, re.DOTALL)
        ]
    else:
        found = re.findall(r"\|\s*([^|]+?)\s*\|\s*([\d.]+)\s*\|\s*[\d,]+%", body)
    for name, votes in found:
        if name in count.votes:
            raise ValueError(f"{name} appears twice")
        count.votes[name.strip()] = number(votes)
    return count


def registered(year: Year) -> int:
    plain = text(read(url(year, year.turnout)))
    match = re.search(r"Elettori \(M\+F\):\s*([\d.]+)", plain) or re.search(r"TOTALE\s*\|?\s*([\d.]+)", plain)
    if match is None:
        raise ValueError(f"{year.year}: the turnout page has no registered voters")
    return number(match[1])


@dataclass
class Loaded:
    president: Count
    lists: Count
    registered: int
    comuni: dict[str, tuple[Count, Count]]


def load(year: Year) -> Loaded:
    comuni = {
        code.replace("_", ""): (
            president(read(url(year, year.comune_president.format(code)))),
            lists(read(url(year, year.comune_lists.format(code)))),
        )
        for code in comune_codes(year)
    }
    return Loaded(
        president(read(url(year, year.president))), lists(read(url(year, year.lists))), registered(year), comuni
    )


def differences(year: Year, data: Loaded) -> list[str]:
    """Where the municipalities' figures do not add up to the province's."""
    out = []
    for index, (name, total) in enumerate((("president", data.president), ("lists", data.lists))):
        summed: dict[str, int] = {}
        for pair in data.comuni.values():
            for label, votes in pair[index].votes.items():
                summed[label] = summed.get(label, 0) + votes
        for label in sorted({*summed, *total.votes}):
            if summed.get(label, 0) != total.votes.get(label, 0):
                out.append(f"{year.year} {name} {label}: {summed.get(label, 0)} against {total.votes.get(label, 0)}")
    for field_name in ("blank", "invalid", "voters"):
        summed_figure = sum(getattr(pair[0], field_name) for pair in data.comuni.values())
        if summed_figure != getattr(data.president, field_name):
            out.append(f"{year.year} {field_name}: {summed_figure} against {getattr(data.president, field_name)}")
    return out


# ---------------------------------------------------------------- building the packages


def source_id() -> str:
    return package_id("source", "provincia")


def package_id(kind: str, *parts: object) -> str:
    return Package("", SOURCE, "").id(kind, *parts)


def person_key(name: str) -> str:
    """A candidate's key: the words of the name in alphabetical order, since the sites print the given name first
    until 2013 and the surname first since 2018."""
    return " ".join(sorted(norm(name).split()))


def shown(name: str) -> str:
    return name.title() if name.isupper() else name


def geo() -> Package:
    p = Package(f"{SOURCE}/geo", SOURCE, "The area of the Autonomous Province of Trento, with its outline")
    taa.geo(p, PROVINCE)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The Autonomous Province of Trento's polity, its council and presidency, and the lists that stood in its "
        "provincial elections of 2003 to 2023",
        depends=[f"{SOURCE}/geo"],
    )
    p.add(
        "source",
        id=source_id(),
        kind="official_body",
        publisher="Provincia autonoma di Trento",
        title="Elezioni provinciali: risultati",
        url="https://elezioni.provincia.tn.it/Elezioni-precedenti",
        license=taa.LICENSE,
        attribution="Provincia autonoma di Trento",
    )
    taa.polity(p, PROVINCE, p.id("body", "council"), 35)
    office = p.id("office", "president")
    p.add("office", id=office, polity_id=POLITY, body_id=None, kind="head_of_government")
    p.name(office, "President of the Autonomous Province of Trento", "en", "official")
    p.name(office, "Presidente della Provincia autonoma di Trento", "it", "native", False)
    # The sites print the given name first until 2013, so a candidate's earliest name is the one to show.
    people: dict[str, str] = {}
    for year in YEARS.values():
        for name in president(read(url(year, year.president))).votes:
            people.setdefault(person_key(name), name)
    for key, name in sorted(people.items()):
        person = p.id("person", key)
        p.add("person", id=person)
        p.name(person, shown(name), "it", "official")
    labels: dict[str, str] = {}
    for year in YEARS.values():
        for label in lists(read(url(year, year.lists))).votes:
            current = labels.get(norm(label))
            if current is None or (current.isupper() and not label.isupper()):
                labels[norm(label)] = label
    for key, label in sorted(labels.items()):
        party = p.id("party", key)
        p.add("party", id=party, kind="list_vehicle", polity_id=taa.ITALY)
        p.name(party, label, "it", "official")
    p.external |= {taa.ITALY}
    return p


def election(year_number: int) -> Package:
    year = YEARS[year_number]
    data = load(year)
    p = Package(
        f"{SOURCE}/{year.year}",
        SOURCE,
        f"The {year.year} Trentino provincial election: votes for President and for the council's lists by municipality",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    area = taa.area_of(PROVINCE)
    for page_url in [url(year, path) for path in summary_pages(year)] + [
        url(year, pattern.format(code))
        for code in comune_codes(year)
        for pattern in (year.comune_president, year.comune_lists)
    ]:
        p.artifact(raw.cached(SOURCE, page_url), source_id())
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
    p.name(election_id, f"{year.year} Trentino provincial election", "en", "official")
    p.name(election_id, f"Elezioni provinciali in Trentino del {year.year}", "it", "native", False)
    path = f"{PROVINCE.path}/{year.day}-general"
    p.add("slug", path=path, entity_id=election_id, is_current=True)
    note = (
        f"Votes are as the Autonomous Province of Trento's results site for the {year.year} election prints them, by "
        "municipality. Null ballots include ballots with only null votes and any contested votes not assigned. The "
        f"site gives registered voters only for the whole province: {data.registered:,} on its turnout page."
    )
    p.add("note", id=p.id("note", year.year), entity_id=election_id, kind="methodology", body_md=note, lang="en")
    contests = {
        "president": p.id("contest", year.year, "president"),
        "council": p.id("contest", year.year, "council"),
    }
    p.add(
        "contest",
        id=contests["president"],
        election_id=election_id,
        kind="office",
        office_id=p.id("office", "president"),
        district_area_id=area,
        magnitude=1,
        default_result_kind="counted",
    )
    p.name(
        contests["president"], f"{year.year} Trentino provincial election, President of the Province", "en", "official"
    )
    p.add("slug", path=f"{path}/presidente", entity_id=contests["president"], is_current=True)
    p.add(
        "contest",
        id=contests["council"],
        election_id=election_id,
        kind="list_allocation",
        body_id=p.id("body", "council"),
        district_area_id=area,
        magnitude=year.seats,
        default_result_kind="counted",
    )
    p.name(contests["council"], f"{year.year} Trentino provincial election, council lists", "en", "official")
    p.add("slug", path=f"{path}/consiglio", entity_id=contests["council"], is_current=True)
    sets = {name: p.id("result_set", year.year, name) for name in contests}
    for name, contest in contests.items():
        p.add(
            "result_set",
            id=sets[name],
            election_id=election_id,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=source_id(),
            status="active",
            is_default=True,
            completeness=1.0,
        )
    options: dict[tuple[str, str], str] = {}
    for position, name in enumerate(data.president.votes, 1):
        option = options["president", name] = p.id("option", year.year, "candidate", person_key(name))
        person = p.id("person", person_key(name))
        p.add(
            "contest_option",
            id=option,
            contest_id=contests["president"],
            kind="candidate",
            ballot_position=position,
            ballot_label=name,
        )
        p.add(
            "candidacy",
            id=p.id("candidacy", year.year, person_key(name)),
            person_id=person,
            option_id=option,
            role="head",
            ballot_name=name,
        )
    for position, label in enumerate(data.lists.votes, 1):
        options["council", label] = p.id("option", year.year, "list", norm(label))
        p.add(
            "contest_option",
            id=options["council", label],
            contest_id=contests["council"],
            kind="list",
            party_id=p.id("party", norm(label)),
            ballot_position=position,
            ballot_label=label,
        )

    def put(result_set: str, unit: str, option: str | None, measure: int, value: int) -> None:
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
    for code, (chosen, listed) in data.comuni.items():
        unit = taa.unit(code, year.day, known)
        p.external.add(unit)
        for name, votes in chosen.votes.items():
            put(sets["president"], unit, options["president", name], VOTES, votes)
        put(sets["president"], unit, None, BLANK, chosen.blank)
        put(sets["president"], unit, None, INVALID, chosen.invalid)
        put(sets["president"], unit, None, VOTERS, chosen.voters)
        for label, votes in listed.votes.items():
            put(sets["council"], unit, options["council", label], VOTES, votes)

    def declare(result_set: str, option: str | None, measure: int, value: int) -> None:
        p.add("declared", result_set_id=result_set, option_id=option, measure=measure, value=value)

    for name, votes in data.president.votes.items():
        declare(sets["president"], options["president", name], VOTES, votes)
    declare(sets["president"], None, BLANK, data.president.blank)
    declare(sets["president"], None, INVALID, data.president.invalid)
    declare(sets["president"], None, VOTERS, data.president.voters)
    for label, votes in data.lists.votes.items():
        declare(sets["council"], options["council", label], VOTES, votes)
    gaps = differences(year, data)
    if gaps:
        print("\n".join(gaps))
    return p
