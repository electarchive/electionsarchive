"""Oslo's district council (bydelsutvalg) elections from Oslo kommune's results pages and election-night protocols."""

import functools
import html
import re
import subprocess
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import date, timedelta
from urllib.parse import urljoin
from uuid import uuid5

import polars as pl

from ea import changeset, package, raw
from ea.package import Package

SOURCE = "no.oslo"
BASE = "https://www.oslo.kommune.no/politikk/valg/valgresultater-og-statistikk/"
YEARS = (2007, 2011, 2015, 2019, 2023)
PAGES = {
    2007: "valgresultater-2007/",
    2011: "valgresultater-2011/",
    2015: "valgresultater-2015/kandidatoversikt-for-bydelsutvalgene-for-perioden-2015-2019/",
    2019: "valgresultater-2019/resultater-for-valg-til-bydelsutvalg-2019/",
    2023: "valgresultater-2023/resultater-for-valg-til-bydelsutvalg-2023/",
}
WORKERS = 4


@dataclass(frozen=True)
class Link:
    year: int
    url: str
    text: str


def page(year: int) -> raw.Artifact:
    return raw.cached(SOURCE, BASE + PAGES[year])


def links(year: int) -> list[Link]:
    text = page(year).path.read_text()
    found = []
    for m in re.finditer(r'<a [^>]*href="([^"]+)"[^>]*>(.*?)</a>', text, re.DOTALL):
        href = html.unescape(m.group(1))
        if "/get-file/" in href:
            label = " ".join(html.unescape(re.sub(r"<[^>]+>", "", m.group(2))).split())
            found.append(Link(year, urljoin(BASE, href), label))
    return found


def fetch() -> None:
    for year in YEARS:
        raw.fetch(SOURCE, BASE + PAGES[year], suffix=".html")
    urls = [link.url for year in YEARS for link in links(year)]
    with ThreadPoolExecutor(WORKERS) as pool:
        list(pool.map(lambda url: raw.fetch(SOURCE, url, suffix=".pdf"), urls))
    print(f"{SOURCE}: {len(urls)} files", flush=True)


# Parsing.

NORWAY = str(uuid5(package.NAMESPACE, "polity\x1fNO"))
COUNTRY = str(uuid5(package.NAMESPACE, "area:world.country@None\x1fNOR"))
OSLO_SOURCE = str(uuid5(package.NAMESPACE, f"{SOURCE}\x1fsource\x1f{SOURCE}"))
BYDEL = "no.bydel"
KRETS = "no.bydelskrets"
SCHEMES = {
    BYDEL: (2, "administrative", "borough", "bydel"),
    KRETS: (3, "precinct", "polling circuit", None),
}
# The 15 bydeler by Statistics Norway's code (classification 103), as the authority names them.
BYDELS = {
    "030101": "Gamle Oslo",
    "030102": "Grünerløkka",
    "030103": "Sagene",
    "030104": "St. Hanshaugen",
    "030105": "Frogner",
    "030106": "Ullern",
    "030107": "Vestre Aker",
    "030108": "Nordre Aker",
    "030109": "Bjerke",
    "030110": "Grorud",
    "030111": "Stovner",
    "030112": "Alna",
    "030113": "Østensjø",
    "030114": "Nordstrand",
    "030115": "Søndre Nordstrand",
}
DAYS = {
    2007: date(2007, 9, 10),
    2011: date(2011, 9, 12),
    2015: date(2015, 9, 14),
    2019: date(2019, 9, 9),
    2023: date(2023, 9, 11),
}
# Elections that ran on a Sunday and a Monday: the first day is the day before.
TWO_DAYS = {2007, 2019, 2023}
EARLY, DAY = 2, 1
VOTES, REGISTERED, VALID, INVALID, BLANK, VOTERS = 1, 2, 4, 5, 6, 14


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


def fold(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text)).strip().casefold()


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def bydel_code(text: str) -> str:
    """The code of the bydel a document names, with or without the word Bydel before it."""
    name = fold(re.sub(r"^bydel\s+", "", clean(text), flags=re.IGNORECASE).rstrip("."))
    for code, official in BYDELS.items():
        if fold(official) == name:
            return code
    raise ValueError(f"unknown bydel {text!r}")


def num(text: str) -> int:
    """A count printed with a point or a space between thousands."""
    return int(re.sub(r"[.\s\u00a0]", "", text))


# The party abbreviations of the 2007 and 2011 reports and the names of every ballot, by folded name.
PARTIES: dict[str, tuple[str, str]] = {
    "dna": ("arbeiderpartiet", "Arbeiderpartiet"),
    "det norske arbeiderparti": ("arbeiderpartiet", "Arbeiderpartiet"),
    "arbeiderpartiet": ("arbeiderpartiet", "Arbeiderpartiet"),
    "sv": ("sv", "Sosialistisk Venstreparti"),
    "sosialistisk venstreparti": ("sv", "Sosialistisk Venstreparti"),
    "sv - sosialistisk venstreparti": ("sv", "Sosialistisk Venstreparti"),
    "sp": ("senterpartiet", "Senterpartiet"),
    "senterpartiet": ("senterpartiet", "Senterpartiet"),
    "krf": ("kristelig folkeparti", "Kristelig Folkeparti"),
    "kristelig folkeparti": ("kristelig folkeparti", "Kristelig Folkeparti"),
    "v": ("venstre", "Venstre"),
    "venstre": ("venstre", "Venstre"),
    "h": ("høyre", "Høyre"),
    "høyre": ("høyre", "Høyre"),
    "frp": ("fremskrittspartiet", "Fremskrittspartiet"),
    "fremskrittspartiet": ("fremskrittspartiet", "Fremskrittspartiet"),
    "dem": ("demokratene", "Demokratene"),
    "demokratene": ("demokratene", "Demokratene"),
    "mdg": ("miljøpartiet de grønne", "Miljøpartiet De Grønne"),
    "miljøpartiet de grønne": ("miljøpartiet de grønne", "Miljøpartiet De Grønne"),
    "pp": ("pensjonistpartiet", "Pensjonistpartiet"),
    "pensjonistpartiet": ("pensjonistpartiet", "Pensjonistpartiet"),
    "rv": ("rødt", "Rødt"),
    "rød valgallianse": ("rødt", "Rødt"),
    "rødt": ("rødt", "Rødt"),
    "osby": ("oslo byaksjon", "Oslo Byaksjon"),
}


@dataclass
class Unit:
    code: str
    name: str
    registered: int | None
    votes: dict[str, dict[int, int]]


@dataclass
class Result:
    """One bydelsutvalg election as the authority publishes it."""

    year: int
    code: str
    urls: list[str]
    labels: list[str]
    seats: dict[str, int]
    totals: dict[str, int]
    registered: int | None = None
    units: list[Unit] = field(default_factory=list)
    # Channel totals of a result that has no units of its own: label -> channel -> votes.
    channels: dict[str, dict[int, int]] = field(default_factory=dict)
    blank: dict[int, int] = field(default_factory=dict)
    invalid: dict[int, int] = field(default_factory=dict)
    voters: int | None = None


def text_of(url: str) -> str:
    path = raw.cached(SOURCE, url).path
    return subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, capture_output=True, text=True).stdout


def party(label: str) -> tuple[str, str]:
    name = fold(label)
    return PARTIES.get(name, (name, clean(label)))


NUMBER = r"\d[\d.]*"
ROW = re.compile(rf"^\s*(\S.*?)\s+((?:{NUMBER}\s+){{6}}{NUMBER})(?:\s+[\d.,]+)?\s*$")


def report(text: str, title: str) -> list[str]:
    """The pages of a 2007 or 2011 document that hold one report, by its heading."""
    return [
        page
        for page in text.split("\f")
        if re.search(rf"ENDELIGE RESULTATER - {re.escape(title)}\s*$", page, re.MULTILINE)
    ]


def split_votes(numbers: list[int]) -> dict[int, int]:
    """Early and election-day votes of a row of eight counts: each channel's unaltered and altered ballots."""
    return {EARLY: numbers[0] + numbers[1], DAY: numbers[2] + numbers[3]}


def old_rows(page: str) -> tuple[dict[str, dict[int, int]], dict[int, int]]:
    rows: dict[str, dict[int, int]] = {}
    total: dict[int, int] = {}
    for line in page.splitlines():
        m = ROW.match(line.replace("----", "0"))
        if not m:
            continue
        numbers = [num(n) for n in m.group(2).split()]
        votes = split_votes(numbers)
        if numbers[6] != votes[EARLY] + votes[DAY] or numbers[4] + numbers[5] != numbers[6]:
            raise ValueError(f"row does not add up: {line!r}")
        if m.group(1) in ("Total", "Totaler"):
            total = votes
        else:
            rows[m.group(1)] = votes
    return rows, total


def parse_old(year: int, links_: dict[str, list[Link]]) -> list[Result]:
    """The 2007 and 2011 election reports: seats, votes per list, and votes per polling circuit."""
    found = []
    for texts in links_.values():
        urls = [link.url for link in texts]
        text = "\f".join(text_of(url) for url in urls)
        name = re.search(r"Valgoppgjør for (.+?)\s*$", text, re.MULTILINE)
        assert name
        code = bydel_code(name.group(1))
        seats_page = report(text, "Mandatfordeling")[0]
        seats = {}
        for line in seats_page.splitlines():
            m = re.match(rf"^(\S+)\s+(\S.*?)\s{{2,}}{NUMBER}\s+{NUMBER}\s+{NUMBER}\s+(\d+)\s*$", line)
            if m:
                seats[party(m.group(1))[0]] = int(m.group(3))
        by_list, _ = old_rows(report(text, "Partifordeling")[0])
        for label in by_list:
            party(label)
        registered = re.search(r"Antall stemmeberettigede:\s+(\S+)", report(text, "Partifordeling")[0])
        assert registered
        result = Result(
            year,
            code,
            urls,
            list(by_list),
            seats,
            {party(label)[0]: sum(v.values()) for label, v in by_list.items()},
            num(registered.group(1)),
        )
        for page in report(text, "Partifordeling pr. krets"):
            for block in re.split(r"(?=^[ \t]*Krets:)", page, flags=re.MULTILINE)[1:]:
                head = re.match(r"\s*Krets:\s+(\d+)\s+(.*?)\s*$", block.splitlines()[0])
                assert head
                voters = re.search(r"Antall stemmeberettigede:\s+(\d[\d.]*)", block)
                rows, unit_total = old_rows(block)
                result.units.append(
                    Unit(
                        head.group(1),
                        clean(head.group(2)),
                        num(voters.group(1)) if voters else None,
                        {party(label)[0]: v for label, v in rows.items()},
                    )
                )
                for channel in (EARLY, DAY):
                    if sum(v[channel] for v in rows.values()) != unit_total.get(channel, 0):
                        raise ValueError(f"{year} {code} circuit {head.group(1)} does not add up")
        for label in by_list:
            k = party(label)[0]
            for channel in (EARLY, DAY):
                if sum(u.votes.get(k, {}).get(channel, 0) for u in result.units) != by_list[label][channel]:
                    raise ValueError(f"{year} {code} {label}: circuits do not add up to the bydel")
        found.append(result)
    return found


def parse_2015(year: int, links_: list[Link]) -> list[Result]:
    """The 2015 candidate sheets: ballots for each list, and the members the authority marks as elected."""
    found = []
    for link in links_:
        text = text_of(link.url)
        head = re.search(r"^\s*(0301\d\d)\s+(.+?)\s*$", text, re.MULTILINE)
        assert head
        code = head.group(1)
        if fold(BYDELS[code]).replace(" ", "") != fold(head.group(2)).replace(" ", ""):
            raise ValueError(f"{link.url}: {head.group(2)} is not {BYDELS[code]}")
        labels: list[str] = []
        totals: dict[str, int] = {}
        seats: dict[str, int] = {}
        for block in re.split(r"(?=^[ \t]*Valglistens navn:)", text, flags=re.MULTILINE)[1:]:
            label = re.match(r"\s*Valglistens navn:\s*(.+?)\s*$", block.splitlines()[0])
            ballots = re.search(r"Antall stemmesedler:\s*(\d+)", block)
            assert label and ballots
            k = party(label.group(1))[0]
            labels.append(label.group(1))
            totals[k] = int(ballots.group(1))
            seats[k] = len(re.findall(r"^\s*\d+\s+.+?\s+\*\*\s", block, re.MULTILINE))
        found.append(Result(year, code, [link.url], labels, {k: n for k, n in seats.items() if n}, totals))
    return found


def parse_protocol(year: int, links_: list[Link]) -> list[Result]:
    """The 2019 and 2023 protocols of each bydel's electoral board."""
    found = []
    for link in links_:
        if "valgprotokoll" not in fold(link.text):
            continue
        text = text_of(link.url)
        named = re.search(r"for (Bydel .+?)\.?\s*\(PDF\)", link.text)
        assert named
        code = bydel_code(named.group(1))
        section = text.split("D3 ", 1)[1].split("Totalt antall partifordelte", 1)[0]
        labels: list[str] = []
        totals: dict[str, int] = {}
        channels: dict[str, dict[int, int]] = {}
        for line in section.splitlines():
            m = re.match(r"^\s*(\S.*?)\s{2,}(\d+)\s+(\d+)\s+(\d+)\s*$", line)
            if not m or m.group(1) == "Parti":
                continue
            k = party(m.group(1))[0]
            labels.append(m.group(1))
            channels[k] = {EARLY: int(m.group(2)), DAY: int(m.group(3))}
            totals[k] = int(m.group(4))
            if channels[k][EARLY] + channels[k][DAY] != totals[k]:
                raise ValueError(f"{link.url}: {m.group(1)} does not add up")
        grand = re.search(r"Totalt antall partifordelte stemmesedler\s+(\d+)\s+(\d+)\s+(\d+)", text.split("D3 ", 1)[1])
        assert grand
        if sum(c[EARLY] for c in channels.values()) != int(grand.group(1)) or sum(totals.values()) != int(
            grand.group(3)
        ):
            raise ValueError(f"{link.url}: list votes do not add up to the printed total")
        blank: dict[int, int] = {}
        invalid: dict[int, int] = {}
        for channel, row in ((EARLY, "(?: forhåndsstemmesedler)?"), (DAY, " valgtingsstemmesedler")):
            m = re.search(rf"Totalt antall opptalte{row}\s+(\d+)\s+(\d+)\s+(\d+)\s+(\d+)", text)
            assert m
            if int(m.group(1)) != sum(c[channel] for c in channels.values()):
                raise ValueError(f"{link.url}: approved ballots disagree with the list votes")
            blank[channel], invalid[channel] = int(m.group(2)), int(m.group(3))
        seats: dict[str, int] = {}
        for line in text.split("Mandatene ble fordelt som følger", 1)[1].split("Kandidatkåring", 1)[0].splitlines():
            m = re.match(r"^\s*\d+\s+(\S.*?)\s{2,}[\d ]+,\d+\s*$", line)
            if m:
                k = party(m.group(1))[0]
                seats[k] = seats.get(k, 0) + 1
        registered = re.search(r"Antall stemmeberettigede\s+([\d ]+?)\s*$", text, re.MULTILINE)
        voted = [
            int(n.replace(" ", ""))
            for n in re.findall(
                r"Totalt antall godkjente (?:forhånds|valgtings)stemmegivninger\s+([\d ]+?)\s*$", text, re.MULTILINE
            )
        ]
        found.append(
            Result(
                year,
                code,
                [link.url],
                labels,
                seats,
                totals,
                num(registered.group(1)) if registered else None,
                channels=channels,
                blank=blank,
                invalid=invalid,
                voters=sum(voted) if len(voted) == 2 else None,
            )
        )
    return found


@functools.cache
def results(year: int) -> list[Result]:
    ls = links(year)
    if year == 2007:
        # The first set of three files is the city council's; each bydel's follows as three files.
        groups = {f"{i}": ls[3 * i : 3 * i + 3] for i in range(1, 16)}
        for group in groups.values():
            kinds = [fold(link.text).split()[0] for link in group]
            if kinds != ["mandatfordeling,", "samleliste", "partisammendrag"]:
                raise ValueError(f"unexpected files {[link.text for link in group]}")
        found = parse_old(year, groups)
    elif year == 2011:
        found = parse_old(year, {link.text: [link] for link in ls if link.text.startswith("Bydel ")})
    elif year == 2015:
        found = parse_2015(year, ls)
    else:
        found = parse_protocol(year, ls)
    if sorted(r.code for r in found) != sorted(BYDELS):
        raise ValueError(f"{year}: found {sorted(r.code for r in found)}")
    return sorted(found, key=lambda r: r.code)


def day_start(year: int) -> date:
    return DAYS[year] - timedelta(days=1) if year in TWO_DAYS else DAYS[year]


def packages() -> list[str]:
    return [f"{SOURCE}/geo", f"{SOURCE}/registry", *(f"{SOURCE}/{DAYS[year]}" for year in YEARS)]


def area(code: str) -> str:
    return key("area", BYDEL, code)


def body(code: str) -> str:
    return key("body", code)


def party_id(k: str) -> str:
    return key("party", k)


SYSTEM = key("electoral_system", "bydelsutvalg")
LICENSE = "Public election results of Oslo kommune; no licence is stated"
GROUPS = {"oslo byaksjon"}
METHOD = (
    "Counted by the bydel electoral boards of Oslo kommune and published by the municipality, from the final count. "
    "The votes of a list are the ballots (stemmesedler) cast for it. A ballot may carry personal votes, and the "
    "authority adds the votes that lists give each other (slengere) when it allocates seats; neither is stored here, "
    "so the list votes are the ballots counted. Seats are the authority's own allocation by the modified "
    "Sainte-Laguë method with a first divisor of 1.4. Early (forhånd) and election-day (valgting) votes are stored "
    "apart where the authority publishes them. Where it publishes polling circuits (krets), each circuit is a unit, "
    "and ballots that are not assigned to a circuit (early votes not distributed to a circuit, election-day votes "
    "not distributed, and voters from other bydeler) are aggregate units of their own."
)
OLD_METHOD = (
    " The 2007 and 2011 reports give every list's ballots in each polling circuit, split by channel; each circuit's "
    "registered voters are the circuit's electoral roll. A circuit's registered voters are not published for the "
    "aggregate units."
)
PROTOCOL_METHOD = (
    " The protocol of the bydel's electoral board gives each list's early and election-day ballots for the whole "
    "bydel, with blank and rejected ballots. Blank ballots and rejected ballots are stored apart from the list votes. "
    "Voters are the approved voter registrations (godkjente stemmegivninger), which include voters who cast no "
    "ballot for the bydelsutvalg."
)
CANDIDATE_METHOD = (
    " The 2015 figures come from the authority's candidate sheets, which print each list's ballots and mark the "
    "members elected; the seats are those marks."
)


def _source(p: Package) -> None:
    p.add(
        "source",
        id=OSLO_SOURCE,
        kind="official_body",
        publisher="Oslo kommune",
        title="Valgresultater og statistikk",
        url=BASE,
        license=LICENSE,
        attribution="Oslo kommune",
    )


def geo() -> Package:
    p = Package(f"{SOURCE}/geo", SOURCE, "Oslo's 15 bydeler (boroughs) as Statistics Norway codes them")
    p.external |= {NORWAY, COUNTRY}
    _source(p)
    for code, (rank, kind, noun, system) in SCHEMES.items():
        p.add("scheme", code=code, polity_id=NORWAY, kind=kind, rank=rank, noun=noun, code_system=system)
    for code, name in BYDELS.items():
        id = area(code)
        p.add("area", id=id, scheme_code=BYDEL)
        p.add("area_code", area_id=id, code=code)
        p.add("area_parent", area_id=id, parent_id=COUNTRY)
        p.name(id, name, "nb", "official")
        p.name(id, f"Bydel {name}", "nb", "official", False)
    return p


def registry() -> Package:
    p = Package(
        f"{SOURCE}/registry",
        SOURCE,
        "The district councils (bydelsutvalg) of Oslo, the lists that stood for them from 2007 to 2023, and their "
        "electoral system",
        depends=[f"{SOURCE}/geo"],
    )
    p.external |= {NORWAY}
    for code, name in BYDELS.items():
        p.add("body", id=body(code), polity_id=NORWAY, kind="council")
        p.name(body(code), f"Bydelsutvalget, {name}", "nb", "official")
    p.add(
        "electoral_system",
        id=SYSTEM,
        polity_id=NORWAY,
        valid_from=date(2007, 1, 1),
        valid_to=None,
        rules={"formula": "modified_sainte_lague", "firstDivisor": 1.4},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", SYSTEM),
        entity_id=SYSTEM,
        kind="methodology",
        body_md=(
            "The 15 seats of each bydelsutvalg are elected on lists in one district, the bydel, with seats "
            "allocated by the modified Sainte-Laguë method with a first divisor of 1.4."
        ),
        lang="en",
    )
    names: dict[str, dict[int, str]] = {}
    for year in YEARS:
        for result in results(year):
            for label in result.labels:
                k, display = party(label)
                names.setdefault(k, {})[year] = display
    for k, by_year in sorted(names.items()):
        id = party_id(k)
        p.add("party", id=id, kind="independent_group" if k in GROUPS else "party", polity_id=NORWAY)
        p.add("external_id", entity_id=id, scheme="no.oslo.party", value=k)
        latest = by_year[max(by_year)]
        p.name(id, latest, "nb", "official")
        for text in sorted(set(by_year.values()) - {latest}):
            p.name(id, text, "nb", "official", False)
    return p


Fact = tuple[str, str, str | None, int, int, int]


def election(year: int) -> Package:
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{day}",
        SOURCE,
        f"The {day:%-d %B %Y} elections to Oslo's 15 district councils",
        depends=[f"{SOURCE}/geo", f"{SOURCE}/registry"],
    )
    p.external |= {NORWAY}
    p.artifact(page(year), OSLO_SOURCE)
    election_id = key("election", day)
    p.add(
        "election",
        id=election_id,
        polity_id=NORWAY,
        kind="local",
        date_from=day_start(year),
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election_id, f"{year} Oslo district council elections", "en", "official")
    p.name(election_id, f"Bydelsutvalgsvalgene i Oslo {year}", "nb", "native", False)
    p.add("slug", path=f"no/{day}-oslo-district-councils", entity_id=election_id, is_current=True)
    method = METHOD + (OLD_METHOD if year < 2015 else CANDIDATE_METHOD if year == 2015 else PROTOCOL_METHOD)
    facts: list[Fact] = []
    declared: list[tuple[str, str | None, int, int]] = []
    for result in results(year):
        for url in result.urls:
            p.artifact(raw.cached(SOURCE, url), OSLO_SOURCE)
        name = BYDELS[result.code]
        contest_id = key("contest", day, result.code)
        p.add(
            "contest",
            id=contest_id,
            election_id=election_id,
            kind="list_allocation",
            body_id=body(result.code),
            district_area_id=area(result.code),
            magnitude=sum(result.seats.values()),
            round_number=1,
            electoral_system_id=SYSTEM,
            default_result_kind="counted",
        )
        p.name(contest_id, f"Bydelsutvalget, {name}", "nb", "official")
        p.add(
            "slug",
            path=f"no/{day}-oslo-district-councils/{changeset.slug(name)}",
            entity_id=contest_id,
            is_current=True,
        )
        set_id = key("result_set", day, result.code)
        p.add(
            "result_set",
            id=set_id,
            election_id=election_id,
            contest_id=contest_id,
            stage="official",
            kind="counted",
            source_id=OSLO_SOURCE,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        p.add("note", id=key("note", set_id), entity_id=set_id, kind="methodology", body_md=method, lang="en")
        options: dict[str, str] = {}
        for label in result.labels:
            k, _ = party(label)
            options[k] = option_id = key("option", day, result.code, k)
            p.add(
                "contest_option",
                id=option_id,
                contest_id=contest_id,
                kind="list",
                party_id=party_id(k),
                ballot_label=label,
            )
            if k in result.seats:
                p.add("outcome", result_set_id=set_id, option_id=option_id, kind="won", seats=result.seats[k])
        if result.units:
            for unit in result.units:
                votes = {k: v for k, v in unit.votes.items()}
                live = [c for c in (EARLY, DAY) if any(v.get(c, 0) for v in votes.values())]
                if not live and unit.registered is None:
                    continue
                unit_id = key("unit", day, result.code, unit.code)
                aggregate = unit.code[:2] in ("95", "96", "97")
                p.add(
                    "reporting_unit",
                    id=unit_id,
                    polity_id=NORWAY,
                    scheme_code=KRETS,
                    code=f"{day}:{result.code}:{unit.code}",
                    area_relation="within",
                    area_id=area(result.code),
                    kind="aggregate" if aggregate else "standard",
                )
                p.name(unit_id, unit.name, "nb", "official")
                for c in live:
                    for k, v in votes.items():
                        facts.append((set_id, unit_id, options[k], c, VOTES, v.get(c, 0)))
                    facts.append((set_id, unit_id, None, c, VALID, sum(v.get(c, 0) for v in votes.values())))
                if unit.registered is not None:
                    facts.append((set_id, unit_id, None, 0, REGISTERED, unit.registered))
        else:
            unit_id = key("unit", day, result.code, "bydel")
            p.add(
                "reporting_unit",
                id=unit_id,
                polity_id=NORWAY,
                scheme_code=BYDEL,
                code=f"{day}:{result.code}",
                area_relation="equals",
                area_id=area(result.code),
                kind="standard",
            )
            p.name(unit_id, name, "nb", "official")
            if result.channels:
                for k, split in result.channels.items():
                    for c, v in split.items():
                        facts.append((set_id, unit_id, options[k], c, VOTES, v))
                for c in (EARLY, DAY):
                    facts.append((set_id, unit_id, None, c, VALID, sum(split[c] for split in result.channels.values())))
                    facts.append((set_id, unit_id, None, c, BLANK, result.blank[c]))
                    facts.append((set_id, unit_id, None, c, INVALID, result.invalid[c]))
            else:
                for k, v in result.totals.items():
                    facts.append((set_id, unit_id, options[k], 0, VOTES, v))
                facts.append((set_id, unit_id, None, 0, VALID, sum(result.totals.values())))
            if result.registered is not None:
                facts.append((set_id, unit_id, None, 0, REGISTERED, result.registered))
            if result.voters is not None:
                facts.append((set_id, unit_id, None, 0, VOTERS, result.voters))
        for k, v in result.totals.items():
            declared.append((set_id, options[k], VOTES, v))
        declared.append((set_id, None, VALID, sum(result.totals.values())))
        if result.registered is not None:
            declared.append((set_id, None, REGISTERED, result.registered))
        if result.channels:
            declared.append((set_id, None, BLANK, sum(result.blank.values())))
            declared.append((set_id, None, INVALID, sum(result.invalid.values())))
        if result.voters is not None:
            declared.append((set_id, None, VOTERS, result.voters))
    p.frame(
        "result_value",
        pl.DataFrame(
            facts,
            schema={
                "result_set_id": pl.String,
                "unit_id": pl.String,
                "option_id": pl.String,
                "channel": pl.Int16,
                "measure": pl.Int16,
                "value": pl.Int64,
            },
            orient="row",
        ).with_columns(count_round=pl.lit(0, pl.Int16), flags=pl.lit(0, pl.Int16)),
    )
    p.frame(
        "declared",
        pl.DataFrame(
            declared,
            schema={"result_set_id": pl.String, "option_id": pl.String, "measure": pl.Int16, "value": pl.Int64},
            orient="row",
        ),
    )
    return p


def parse() -> None:
    geo().write()
    registry().write()
    for year in YEARS:
        election(year).write()


def emit() -> None:
    package.run("load", packages())
