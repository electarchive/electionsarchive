"""Norway's Storting elections of 1906 and 1921-1936, digitized from Statistics Norway's NOS volumes.

Each volume (Norges offisielle statistikk, "Stortingsvalget <year>") is a scanned PDF with a text layer. The figures
were read from the page images and from that text layer, and the transcriptions are committed beside this module:

- `nos_1906.json`: the 1906 election under single-member districts with a second ballot (omvalg). For each of the
  123 districts it holds every candidate for representative with the votes at the first ballot and at the second
  ballot, as printed in the political part of the volume, with the scattered votes ("desuten"). Each district's first
  ballot adds up to the approved votes that Table 5 (rural districts) or Table 6 (towns, by town) prints.
- `nos_1921_1936.json`: the list elections of 1921-1936 under proportional representation in 29 constituencies (18
  rural counties and 11 groups of towns). The summary table of each volume ("Sammendrag av stemmer på de forskjellige
  partier") prints the approved ballots and each party's ballots (stemmesedler) by rural constituency and by town; every
  column adds up to the printed totals. Seats by party are those of the elected representatives (1921-1927).

Every value keeps the page and the box of the printed figure.
"""

import json
import re
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path
from uuid import uuid5

from ea import package, raw
from ea.package import Package

SOURCE = "no.nos"
BASE = "https://www.ssb.no/a/histstat/nos"
VOLUMES = {
    1906: "nos_v_049",
    1909: "nos_v_128",
    1912: "nos_v_189",
    1915: "nos_vi_065",
    1918: "nos_vi_150",
    1921: "nos_vii_066",
    1924: "nos_vii_176",
    1927: "nos_viii_069",
    1930: "nos_viii_157",
    1933: "nos_ix_026",
    1936: "nos_ix_107",
}
BUILT = (1906, 1921, 1924, 1927, 1930, 1933, 1936)
DAYS = {
    1921: date(1921, 10, 24),
    1924: date(1924, 10, 21),
    1927: date(1927, 10, 17),
    1930: date(1930, 10, 20),
    1933: date(1933, 10, 16),
    1936: date(1936, 10, 19),
}
HERE = Path(__file__).parent
DATA_1906 = HERE / "nos_1906.json"
DATA_PR = HERE / "nos_1921_1936.json"
NORWAY = str(uuid5(package.NAMESPACE, "polity\x1fNO"))
# The Storting, as the valgresultat.no pipeline loads it.
STORTING = str(uuid5(package.NAMESPACE, "no.valg\x1fbody\x1fstorting"))
VOTES, VALID = 1, 4
DIGITIZED, ESTIMATED = 8, 2
DISTRICT_SCHEME = "no.valgkrets_1906"
CONSTITUENCY_SCHEME = "no.valgdistrikt_1921"
TOWN_SCHEME = "no.kjopstad_1921"
REGISTRY = f"{SOURCE}/registry"

RURAL = [
    ("ostfold", "Østfold", 6),
    ("akershus", "Akershus", 7),
    ("hedmark", "Hedmark", 7),
    ("opland", "Opland", 6),
    ("buskerud", "Buskerud", 5),
    ("vestfold", "Vestfold", 4),
    ("telemark", "Telemark", 5),
    ("aust-agder", "Aust-Agder", 4),
    ("vest-agder", "Vest-Agder", 4),
    ("rogaland", "Rogaland", 5),
    ("hordaland", "Hordaland", 8),
    ("sogn-og-fjordane", "Sogn og Fjordane", 5),
    ("more", "Møre", 7),
    ("sor-trondelag", "Sør-Trøndelag", 6),
    ("nord-trondelag", "Nord-Trøndelag", 5),
    ("nordland", "Nordland", 8),
    ("troms", "Troms", 5),
    ("finnmark", "Finnmark", 3),
]
# The town constituencies, each with the number of towns the summary table lists for it, in printed order.
TOWNS = [
    ("byer-ostfold-akershus", "Towns of Østfold and Akershus", 4, 5),
    ("oslo", "Oslo (Kristiania)", 7, 1),
    ("byer-hedmark-opland", "Towns of Hedmark and Opland", 3, 4),
    ("byer-buskerud", "Towns of Buskerud", 3, 3),
    ("byer-vestfold", "Towns of Vestfold", 4, 5),
    ("byer-telemark-aust-agder", "Towns of Telemark and Aust-Agder", 5, 8),
    ("byer-vest-agder-rogaland", "Towns of Vest-Agder and Rogaland", 7, 5),
    ("bergen", "Bergen", 5, 1),
    ("byer-more", "Towns of Møre", 3, 3),
    ("byer-trondelag", "Towns of Sør- and Nord-Trøndelag", 5, 2),
    ("byer-nord-norge", "Towns of Nordland, Troms and Finnmark", 4, 6),
]
# The summary tables' party columns, by their printed heading with spaces and dots removed: (party key, kind).
COLUMNS = {
    "HogFV": ("hoire-og-frisindede", "list"),
    "HogHogFV": ("hoire-og-frisindede", "list"),
    "H": ("hoire", "list"),
    "L": ("bondepartiet", "list"),
    "B": ("bondepartiet", "list"),
    "V": ("venstre", "list"),
    "RF": ("radikale-folkeparti", "list"),
    "S": ("socialdemokratiske", "list"),
    "A": ("arbeiderpartiet", "list"),
    "K": ("kommunistiske", "list"),
    "FV": ("frisindede", "list"),
    "FF": ("frisindede", "list"),
    "FFFel": ("frisindede-fedrelandslaget", "list"),
    "NS": ("nasjonal-samling", "list"),
    "Samfp": ("samfundspartiet", "list"),
    "SP": ("samfundspartiet", "list"),
    "KF": ("kristelig-folkeparti", "list"),
    "Forskj": (None, "others"),
    "Ville": (None, "write_in"),
}
PARTIES = {
    "hoire": ("Høire", "H.", "party"),
    "hoire-og-frisindede": ("Høire og Frisindede Venstre", "H. og F. V.", "bloc"),
    "bondepartiet": ("Bondepartiet (Norsk landmandsforbund)", "B.", "party"),
    "venstre": ("Venstre", "V.", "party"),
    "radikale-folkeparti": ("Det radikale folkeparti (Arbeiderdemokratene)", "R. F.", "party"),
    "socialdemokratiske": ("Norges socialdemokratiske arbeiderparti", "S.", "party"),
    "arbeiderpartiet": ("Det norske Arbeiderparti", "A.", "party"),
    "kommunistiske": ("Norges kommunistiske parti", "K.", "party"),
    "frisindede": ("Det frisindede Venstre (Frisindede Folkeparti)", "F. V.", "party"),
    "frisindede-fedrelandslaget": ("Frisindede Folkeparti og Fedrelandslaget", "F. F. og Fe. l.", "bloc"),
    "nasjonal-samling": ("Nasjonal Samling", "N. S.", "party"),
    "samfundspartiet": ("Samfundspartiet", "Samf.p.", "party"),
    "kristelig-folkeparti": ("Kristelig Folkeparti", "Kr. F.", "party"),
    # The 1906 volume's designations of the candidates.
    "samlingspartiet": ("Samlingspartiet", "Saml.", "party"),
    "arbeiderdemokratene": ("Arbeiderdemokratene", "Arb. dem.", "party"),
    "kirkepartiet": ("Kirkepartiet", "Kirke.", "party"),
    "avholdsfolket": ("Avholdsfolket", "Avh.", "independent_group"),
}
PARTY_1906 = {
    "venstre": "venstre",
    "saml": "samlingspartiet",
    "soc": "arbeiderpartiet",
    "arbdem": "arbeiderdemokratene",
    "kirke": "kirkepartiet",
    "avh": "avholdsfolket",
}
# Joint lists whose votes the summary table divides among the parties in proportion to their votes in 1933.
APPORTIONED = {
    (1936, "R", "Buskerud"): {"H", "V", "B"},
    **{(1936, "T", town): {"H", "V", "FFFel"} for town in ("Hamar", "Kongsvinger", "Lillehammer", "Gjøvik")},
    **{(1936, "T", town): {"H", "V"} for town in ("Hønefoss", "Drammen", "Kongsberg")},
}
NOTES_PR = {
    1921: [
        (
            "In Vestfold the Storting ordered a new poll in fifteen parishes on 13 March 1922; the table holds the first "
            "poll of 24 October 1921."
        ),
        (
            "The radical Venstre lists of the towns of Hedmark and Opland and of Telemark and Aust-Agder are counted with "
            "Venstre, and Kristiansund's Samlingsparti with Høire og Frisindede Venstre."
        ),
    ],
    1924: [
        (
            "The radical Venstre lists of the Opland towns are counted with Venstre, Venstre and the Radical People's Party "
            "of Hedmark with Venstre, and Kristiansund's Samlingsparti with Høire og Frisindede Venstre."
        ),
    ],
    1927: [
        (
            "The Storting annulled the election in the towns of Hedmark and Opland; the table holds the first poll of "
            "October 1927, and the new poll of 27 February 1928 returned the same members."
        ),
        (
            "Local conservative lists (Buskerud's liberal Samlingsparti, the Hordaland voters' association, the bourgeois "
            "Samlingsparti of Nordland and of the Møre towns) are counted with Høire og Frisindede Venstre."
        ),
    ],
    1930: [
        (
            "The column Høire og Frisindede Venstre holds Høire alone where the Frisindede Venstre ran its own list, and "
            "the local bourgeois lists of Vestfold, Buskerud, Møre and Nordland."
        ),
    ],
    1933: ["Local conservative lists are counted with Høire, and Venstre and the Radical People's Party with Venstre."],
    1936: [
        (
            "Joint lists are divided among their parties in proportion to the parties' votes in 1933: in rural Buskerud "
            "(Høire, Bondepartiet and Venstre, 23,267 votes), in the towns of Hedmark and Opland (Høire, Frisindede "
            "Folkeparti and Venstre, 4,855) and in the towns of Buskerud (Høire and Venstre, 9,502). Those figures are "
            "flagged as estimated."
        ),
    ],
}


def url(year: int) -> str:
    return f"{BASE}/{VOLUMES[year]}.pdf"


def fetch() -> None:
    for year in VOLUMES:
        raw.fetch(SOURCE, url(year), suffix=".pdf")


def code(heading: str) -> str:
    return re.sub(r"[\s.]", "", heading)


def slugify(text: str) -> str:
    text = text.lower().translate(str.maketrans({"ø": "o", "æ": "ae", "å": "a", "ü": "u"}))
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")


def key(kind: str, *parts: object) -> str:
    return str(uuid5(package.NAMESPACE, "\x1f".join([SOURCE, kind, *(str(part) for part in parts)])))


SOURCE_ID = key("source", SOURCE)
TWO_ROUND = key("electoral_system", "two-round-1906")
DHONDT = key("electoral_system", "dhondt-1921")


def party(name: str) -> str:
    return key("party", name)


def packages() -> list[str]:
    return [REGISTRY, *(f"{SOURCE}/{year}" for year in BUILT)]


def parse() -> None:
    registry().write()
    single_member().write()
    for year in BUILT[1:]:
        proportional(year).write()


def emit() -> None:
    package.run("load", packages())


def registry() -> Package:
    p = Package(
        REGISTRY,
        SOURCE,
        "The NOS Storting election volumes as a source, the districts' schemes, the parties of 1906 and 1921-1936, "
        "and the electoral systems of those years",
    )
    p.external.add(NORWAY)
    p.add(
        "source",
        id=SOURCE_ID,
        kind="official_body",
        publisher="Statistisk sentralbyrå (Statistics Norway) and the Storting's office",
        title="Norges offisielle statistikk: Stortingsvalget 1906-1936",
        url=f"{BASE}/",
        license="Public official statistics; scans published by Statistics Norway",
        license_url="https://www.ssb.no/en/diverse/lisens",
        attribution="Statistisk sentralbyrå, Norges offisielle statistikk",
    )
    for code_, noun, rank in (
        (DISTRICT_SCHEME, "single-member district", 2),
        (CONSTITUENCY_SCHEME, "constituency", 1),
        (TOWN_SCHEME, "town", 3),
    ):
        p.add("scheme", code=code_, polity_id=NORWAY, kind="electoral_district", rank=rank, noun=noun)
    for name, (text, abbreviation, kind) in PARTIES.items():
        id = party(name)
        p.add("party", id=id, kind=kind, polity_id=NORWAY)
        p.name(id, text, "nb", "official")
        p.name(id, abbreviation, "nb", "abbreviation", False)
    p.add(
        "electoral_system",
        id=TWO_ROUND,
        polity_id=NORWAY,
        valid_from=date(1906, 1, 1),
        valid_to=date(1919, 12, 31),
        rules={"formula": "two_round"},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", TWO_ROUND),
        entity_id=TWO_ROUND,
        kind="methodology",
        body_md=(
            "From 1906 to 1918 each Storting member was elected in a single-member district, with a deputy elected on "
            "the same ballot. A candidate needed more than half of the votes at the first ballot; otherwise a second "
            "ballot (omvalg) was held, where the most votes won."
        ),
        lang="en",
    )
    p.add(
        "electoral_system",
        id=DHONDT,
        polity_id=NORWAY,
        valid_from=date(1920, 1, 1),
        valid_to=date(1952, 12, 31),
        rules={"formula": "dhondt", "lists": {"order": "flexible", "vacancy": "next_on_list"}},
        rules_version=1,
    )
    p.add(
        "note",
        id=key("note", DHONDT),
        entity_id=DHONDT,
        kind="methodology",
        body_md=(
            "From 1921 the Storting's 150 members were elected by list proportional representation in 29 "
            "constituencies: 18 rural counties with 100 members and 11 towns or groups of towns with 50. Each ballot "
            "gave its list as many list votes as the constituency had seats, and seats were allocated by the D'Hondt "
            "method on the list votes. Lists could form electoral alliances (listeforbund) from 1930."
        ),
        lang="en",
    )
    return p


def source_pages(p: Package, year: int, artifact_id: str, sizes: dict[str, list[float]], sha: str) -> dict[int, str]:
    ids = {}
    for page, (width, height) in sorted(sizes.items(), key=lambda kv: int(kv[0])):
        id = key("page", year, page)
        ids[int(page)] = id
        p.add(
            "source_page",
            id=id,
            artifact_id=artifact_id,
            page_number=int(page),
            image_key=f"pages/{SOURCE}/{sha}-{page}.png",
            width=round(width * 100 / 72),
            height=round(height * 100 / 72),
        )
    return ids


class Facts:
    """Values and their boxes for one package."""

    def __init__(self, p: Package, pages: dict[int, str], sizes: dict[str, list[float]]) -> None:
        self.p, self.pages, self.sizes = p, pages, sizes

    def add(
        self, result_set: str, unit: str, option: str | None, measure: int, value: int, box: list[float], flags: int
    ) -> None:
        page = int(box[0])
        width, height = self.sizes[str(page)]
        self.p.add(
            "result_value",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            flags=flags,
            value=value,
        )
        self.p.add(
            "value_locator",
            result_set_id=result_set,
            unit_id=unit,
            option_id=option,
            channel=0,
            measure=measure,
            count_round=0,
            page_id=self.pages[page],
            source_id=None,
            x0=round(box[1] / width, 5),
            y0=round(box[2] / height, 5),
            x1=round(box[3] / width, 5),
            y1=round(box[4] / height, 5),
        )


def display(name: str) -> str:
    """ "Krag-Torp, L." as "L. Krag-Torp"."""
    surname, _, given = name.partition(",")
    return f"{given.strip()} {surname.strip()}".strip()


def check_1906(districts: list[dict]) -> None:
    """Each district's first ballot adds up to the approved votes of Table 5, or its town's of Table 6."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for d in districts:
        groups[d["name"].split(":")[0]].append(d)
    for group, members in groups.items():
        printed = {d["printed_total"] for d in members}
        if len(printed) != 1:
            raise ValueError(f"1906 {group}: districts print different totals {printed}")
        summed = sum(d["first_total"] for d in members)
        for d in members:
            parts = sum(c["votes"][0] or 0 for c in d["candidates"]) + (
                (d["scattered"] or {"votes": [0]})["votes"][0] or 0
            )
            if parts != d["first_total"]:
                raise ValueError(f"1906 {d['name']}: candidates add up to {parts}, not {d['first_total']}")
        if summed != printed.pop():
            raise ValueError(f"1906 {group}: first ballot adds up to {summed} against the printed total")


def single_member() -> Package:
    loaded = json.loads(DATA_1906.read_text(encoding="utf-8"))
    districts = loaded["districts"]
    check_1906(districts)
    year = 1906
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        "The 1906 Storting election in 123 single-member districts, both ballots, digitized from the NOS volume",
        depends=[REGISTRY],
    )
    p.external.update({NORWAY, STORTING})
    artifact = raw.cached(SOURCE, url(year))
    artifact_id = p.artifact(artifact, SOURCE_ID)
    pages = source_pages(p, year, artifact_id, loaded["pages"], artifact.sha256)
    facts = Facts(p, pages, loaded["pages"])
    election = key("election", year)
    p.add(
        "election",
        id=election,
        polity_id=NORWAY,
        kind="general",
        date_from=date(year, 1, 1),
        date_to=date(year, 12, 31),
        date_precision="year",
        status="held",
    )
    p.name(election, f"{year} Norwegian parliamentary election", "en", "official")
    p.name(election, f"Stortingsvalget {year}", "nb", "native", False)
    base = f"no/{year}-parliamentary"
    p.add("slug", path=base, entity_id=election, is_current=True)
    p.add(
        "citation",
        id=key("citation", election),
        entity_id=election,
        source_id=SOURCE_ID,
        locator=json.dumps({"url": url(year)}),
    )
    used: Counter[str] = Counter()
    for d in districts:
        slug = slugify(d["name"])
        used[slug] += 1
        if used[slug] > 1:
            raise ValueError(f"{year}: two districts slug as {slug}")
        unit = key("unit", year, d["name"])
        p.add(
            "reporting_unit",
            id=unit,
            polity_id=NORWAY,
            scheme_code=DISTRICT_SCHEME,
            code=f"{year}:{slug}",
            area_relation="unlocated",
            kind="standard",
        )
        p.name(unit, d["name"], "nb", "official")
        for i, c in enumerate(d["candidates"]):
            person = key("person", year, d["name"], i)
            p.add("person", id=person)
            p.name(person, display(c["name"]), "nb", "official")
        held = [r for r in (0, 1) if any(c["votes"][r] is not None for c in d["candidates"])]
        previous = None
        for r in held:
            contest = key("contest", year, d["name"], r + 1)
            p.add(
                "contest",
                id=contest,
                election_id=election,
                kind="body_seats",
                body_id=STORTING,
                magnitude=1,
                round_number=r + 1,
                previous_round_id=previous,
                electoral_system_id=TWO_ROUND,
                default_result_kind="counted",
            )
            previous = contest
            label = "first ballot" if r == 0 else "second ballot"
            p.name(contest, f"Storting, {d['name']} ({label})", "en", "english")
            p.name(contest, f"{d['name']}, {'1ste valg' if r == 0 else 'omvalg'}", "nb", "official", False)
            p.add("slug", path=f"{base}/{slug}{'' if r == 0 else '-omvalg'}", entity_id=contest, is_current=True)
            result_set = key("result_set", year, d["name"], r + 1)
            p.add(
                "result_set",
                id=result_set,
                election_id=election,
                contest_id=contest,
                stage="official",
                kind="counted",
                source_id=SOURCE_ID,
                status="active",
                is_default=True,
                completeness=1.0,
            )
            p.add(
                "note",
                id=key("note", result_set),
                entity_id=result_set,
                kind="methodology",
                body_md=(
                    "Digitized from the political part of NOS Stortingsvalget 1906, which lists every candidate for "
                    "representative with the votes at the first ballot and at the second ballot (omvalg), and the "
                    "scattered votes. The party is the volume's own designation of the candidate (Samlingspartiet, "
                    "Venstre, socialists, labour democrats, the church party, temperance), not a nomination; the full "
                    "designation is kept in the candidacy's ballot label. Every value keeps its page and box."
                ),
                lang="en",
            )
            best: tuple[int, str] | None = None
            for i, c in enumerate(d["candidates"]):
                value = c["votes"][r]
                if value is None:
                    continue
                option = key("option", year, d["name"], r + 1, i)
                person = key("person", year, d["name"], i)
                p.add(
                    "contest_option",
                    id=option,
                    contest_id=contest,
                    kind="candidate",
                    party_id=party(PARTY_1906[c["party"]]) if c["party"] else None,
                    ballot_position=i + 1,
                    ballot_label=display(c["name"]),
                )
                p.add(
                    "candidacy",
                    id=key("candidacy", year, d["name"], r + 1, i),
                    person_id=person,
                    option_id=option,
                    role="head",
                    ballot_name=f"{c['name']}, {c['occupation']} ({c['label']})" if c["label"] else c["name"],
                    member_of_party_id=party(PARTY_1906[c["party"]]) if c["party"] else None,
                )
                facts.add(result_set, unit, option, VOTES, value, c["boxes"][r], DIGITIZED)
                if best is None or value > best[0]:
                    best = (value, option)
            scattered = d["scattered"]
            if scattered and scattered["votes"][r] is not None:
                option = key("option", year, d["name"], r + 1, "desuten")
                p.add("contest_option", id=option, contest_id=contest, kind="others", ballot_label="Scattered votes")
                facts.add(result_set, unit, option, VOTES, scattered["votes"][r], scattered["boxes"][r], DIGITIZED)
            if r == held[-1] and best is not None:
                p.add("outcome", result_set_id=result_set, option_id=best[1], kind="elected")
    return p


def proportional(year: int) -> Package:
    loaded = json.loads(DATA_PR.read_text(encoding="utf-8"))[str(year)]
    day = DAYS[year]
    p = Package(
        f"{SOURCE}/{year}",
        SOURCE,
        f"The {year} Storting election in 29 constituencies, by rural constituency and by town, digitized from the "
        "NOS volume",
        depends=[REGISTRY],
    )
    p.external.update({NORWAY, STORTING})
    artifact = raw.cached(SOURCE, url(year))
    artifact_id = p.artifact(artifact, SOURCE_ID)
    pages = source_pages(p, year, artifact_id, loaded["pages"], artifact.sha256)
    facts = Facts(p, pages, loaded["pages"])
    election = key("election", year)
    p.add(
        "election",
        id=election,
        polity_id=NORWAY,
        kind="general",
        date_from=day,
        date_to=day,
        date_precision="day",
        status="held",
    )
    p.name(election, f"{year} Norwegian parliamentary election", "en", "official")
    p.name(election, f"Stortingsvalget {year}", "nb", "native", False)
    base = f"no/{day}-parliamentary"
    p.add("slug", path=base, entity_id=election, is_current=True)
    p.add(
        "citation",
        id=key("citation", election),
        entity_id=election,
        source_id=SOURCE_ID,
        locator=json.dumps({"url": url(year)}),
    )
    seats = loaded["seats"]
    rural, towns = loaded["rural"], loaded["towns"]
    for table in (rural, towns):
        body = [r for r in table["rows"] if not r["total"]]
        total = [r for r in table["rows"] if r["total"]]
        if len(total) != 1:
            raise ValueError(f"{year}: one printed total row expected")
        for i, heading in enumerate(table["columns"]):
            summed = sum(r["values"][i] or 0 for r in body)
            if summed != (total[0]["values"][i] or 0):
                raise ValueError(f"{year} {heading}: rows add up to {summed}, printed {total[0]['values'][i]}")
    rural_rows = [r for r in rural["rows"] if not r["total"]]
    town_rows = [r for r in towns["rows"] if not r["total"]]
    if len(rural_rows) != len(RURAL) or len(town_rows) != sum(n for *_, n in TOWNS):
        raise ValueError(f"{year}: {len(rural_rows)} rural and {len(town_rows)} town rows")
    groups: list[tuple[str, str, int, str, str, list[dict]]] = [
        (k, name, m, "R", CONSTITUENCY_SCHEME, [row]) for (k, name, m), row in zip(RURAL, rural_rows, strict=True)
    ]
    start = 0
    for k, name, m, n in TOWNS:
        groups.append((k, name, m, "T", TOWN_SCHEME, town_rows[start : start + n]))
        start += n
    if sum(m for _, _, m, *_ in groups) != 150:
        raise ValueError(f"{year}: constituencies do not seat 150")
    for k, name, magnitude, kind, scheme, rows in groups:
        columns = rural["columns"] if kind == "R" else towns["columns"]
        contest = key("contest", year, k)
        p.add(
            "contest",
            id=contest,
            election_id=election,
            kind="list_allocation",
            body_id=STORTING,
            magnitude=magnitude,
            round_number=1,
            electoral_system_id=DHONDT,
            default_result_kind="counted",
        )
        p.name(contest, f"Storting, {name}", "en", "english")
        p.add("slug", path=f"{base}/{k}", entity_id=contest, is_current=True)
        result_set = key("result_set", year, k)
        p.add(
            "result_set",
            id=result_set,
            election_id=election,
            contest_id=contest,
            stage="official",
            kind="counted",
            source_id=SOURCE_ID,
            status="active",
            is_default=True,
            completeness=1.0,
        )
        grain = "the constituency" if kind == "R" else "town"
        body = (
            f"Digitized from the summary table of NOS Stortingsvalget {year} (Sammendrag av stemmer på de forskjellige "
            f"partier), which prints by {grain} the approved ballots (godkjente stemmer, stored as valid ballots) and "
            "each party's ballots (stemmesedler, stored as votes). The columns are the volume's party groups, so a "
            "local list is counted with the party the table assigns it to. The parties' ballots fall a few short of "
            "the approved ballots where ballots could not be assigned to a list. Every column adds up to the printed "
            "national totals, and every value keeps its page and box."
        )
        p.add(
            "note",
            id=key("note", result_set),
            entity_id=result_set,
            kind="methodology",
            body_md=" ".join([body, *NOTES_PR[year]]),
            lang="en",
        )
        options: dict[int, str] = {}
        for i, heading in enumerate(columns):
            c = code(heading)
            if i == 0 or not any(r["values"][i] is not None for r in rows):
                continue
            party_key, option_kind = COLUMNS[c]
            option = key("option", year, k, c)
            options[i] = option
            p.add(
                "contest_option",
                id=option,
                contest_id=contest,
                kind=option_kind,
                party_id=party(party_key) if party_key else None,
                ballot_position=i,
                ballot_label={"Forskj": "Forskjellige (other lists)", "Ville": "Ville (stray votes)"}.get(c, heading),
            )
        for row in rows:
            unit = key("unit", year, kind, row["name"])
            p.add(
                "reporting_unit",
                id=unit,
                polity_id=NORWAY,
                scheme_code=scheme,
                code=f"{year}:{slugify(row['name'])}",
                area_relation="unlocated",
                kind="standard",
            )
            p.name(unit, row["name"], "nb", "official")
            estimated = APPORTIONED.get((year, kind, row["name"]), set())
            for i, value in enumerate(row["values"]):
                if value is None:
                    continue
                flags = DIGITIZED | (ESTIMATED if code(columns[i]) in estimated else 0)
                if i == 0:
                    facts.add(result_set, unit, None, VALID, value, row["boxes"][i], flags)
                else:
                    facts.add(result_set, unit, options[i], VOTES, value, row["boxes"][i], flags)
        if seats:
            won = seats[k]
            if sum(won.values()) != magnitude:
                raise ValueError(f"{year} {k}: seats {won} do not fill {magnitude}")
            by_code = {code(columns[i]): option for i, option in options.items()}
            for label, count in won.items():
                p.add("outcome", result_set_id=result_set, option_id=by_code[code(label)], kind="won", seats=count)
    p.accept(
        "accounting.votes_valid",
        "The summary table assigns each approved ballot to a party where the list votes allow; the few ballots it "
        "cannot assign (655 in 1921) leave the parties' ballots short of the approved ballots, and the 1936 table's "
        "proportional division of joint lists rounds to whole ballots. Both are stored as printed.",
    )
    return p
